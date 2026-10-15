// 用法见 README「重新生成地图数据」：在 tools/ 下 npm i d3-geo@3 topojson-client@3 topojson-simplify@3（package.json 设 "type":"module"），
// 把三个输入文件放进 tools/data/，再 node build_geodata.mjs。
// 预处理地图数据 → 仓库根目录 geodata.py（运行时零依赖）。投影一律用 d3-geo 官方实现。
import fs from "fs";
import { geoPath, geoAlbersUsa, geoNaturalEarth1 } from "d3-geo";
import * as topojson from "topojson-client";
import { presimplify, simplify, quantile } from "topojson-simplify";

const HERE = new URL(".", import.meta.url).pathname;
const D = HERE + "data/";
const load = f => JSON.parse(fs.readFileSync(D + f, "utf8"));

// geoPath 的 context：把（裁剪/重采样后的）路径收集成多个闭合环
function rings(path, obj) {
  const out = []; let cur = null;
  const ctx = {
    moveTo(x, y) { cur = [x, y]; out.push(cur); },
    lineTo(x, y) { cur.push(x, y); },
    closePath() {}, arc() {}, rect() {},
  };
  path.context(ctx)(obj);
  // 保留 1 位小数，去掉少于 3 个点的环
  return out.filter(r => r.length >= 6).map(r => r.map(v => Math.round(v * 10) / 10));
}

// ---------- 美国：已按 geoAlbersUsa().scale(1300).translate([487.5,305]) 投影 ----------
let us = load("states-albers-10m.json");
us = simplify(presimplify(us), quantile(presimplify(us), 0.25)); // 去掉 75% 的点，280px 宽下看不出差别
const usPath = geoPath(null);
const states = {};
for (const f of topojson.feature(us, us.objects.states).features)
  states[f.id] = { name: f.properties.name, rings: rings(usPath, f) };
const nation = rings(usPath, topojson.mesh(us, us.objects.nation));

// ---------- 世界：Natural Earth 投影，排除南极洲，铺满 960x500 ----------
let w = load("countries-50m.json");
w = simplify(presimplify(w), quantile(presimplify(w), 0.15));
const land = topojson.feature(w, w.objects.countries).features.filter(f => f.properties.name !== "Antarctica");
const proj = geoNaturalEarth1().fitExtent([[0, 0], [960, 500]], { type: "FeatureCollection", features: land });
const wPath = geoPath(proj);
const world = {};
for (const f of land) {
  if (!f.id) continue;
  const rs = rings(wPath, f);
  if (rs.length) world[f.id] = rs;
}

// ---------- 代码对照 ----------
const cc2num = {};
for (const c of load("countries.json")) if (c.cca2 && c.ccn3) cc2num[c.cca2] = c.ccn3;
const STATE_FIPS = { AL:"01",AK:"02",AZ:"04",AR:"05",CA:"06",CO:"08",CT:"09",DE:"10",DC:"11",FL:"12",GA:"13",HI:"15",ID:"16",IL:"17",IN:"18",IA:"19",KS:"20",KY:"21",LA:"22",ME:"23",MD:"24",MA:"25",MI:"26",MN:"27",MS:"28",MO:"29",MT:"30",NE:"31",NV:"32",NH:"33",NJ:"34",NM:"35",NY:"36",NC:"37",ND:"38",OH:"39",OK:"40",OR:"41",PA:"42",RI:"44",SC:"45",SD:"46",TN:"47",TX:"48",UT:"49",VT:"50",VA:"51",WA:"53",WV:"54",WI:"55",WY:"56" };

// ---------- 校验点（Python 运行时投影要与之一致） ----------
const usP = geoAlbersUsa().scale(1300).translate([487.5, 305]);
const checks = {
  us: Object.fromEntries([["KC",[-94.5786,39.0997],"KS"],["LA",[-118.2437,34.0522],"CA"],["NYC",[-74.006,40.7128],"NY"],
    ["Seattle",[-122.3321,47.6062],"WA"],["Miami",[-80.1918,25.7617],"FL"],["Anchorage",[-149.9003,61.2181],"AK"],
    ["Juneau",[-134.4197,58.3019],"AK"],["Honolulu",[-157.8583,21.3069],"HI"],["LasVegas",[-115.1398,36.1699],"NV"]]
    .map(([k, ll, st]) => [k, { ll, st, xy: usP(ll) }])),
  world: Object.fromEntries([["Tokyo",[139.69,35.68]],["HK",[114.17,22.32]],["London",[-0.1276,51.5072]],
    ["Sydney",[151.21,-33.87]],["SaoPaulo",[-46.63,-23.55]],["Reykjavik",[-21.94,64.15]],["Fiji",[178.44,-18.14]]]
    .map(([k, ll]) => [k, { ll, xy: proj(ll) }])),
};

const py = s => JSON.stringify(s);
const out = `# -*- coding: utf-8 -*-
"""地图数据（由 tools/data 下的 us-atlas / world-atlas 经 d3-geo 预处理生成，勿手改）。
美国：us-atlas states-albers-10m，geoAlbersUsa().scale(1300).translate([487.5,305])，画布 975x610。
世界：world-atlas countries-50m（不含南极洲），geoNaturalEarth1 铺满 960x500。
环为扁平坐标列表 [x0, y0, x1, y1, ...]。"""

US_SIZE = (975, 610)
US_STATES = ${py(states)}
US_NATION = ${py(nation)}
STATE_FIPS = ${py(STATE_FIPS)}

WORLD_SIZE = (960, 500)
WORLD_FIT = ${py({ scale: proj.scale(), translate: proj.translate() })}
WORLD = ${py(world)}
CC2NUM = ${py(cc2num)}

CHECKS = ${py(checks)}
`.replace(/\bnull\b/g, "None");
fs.writeFileSync(HERE + "../geodata.py", out);
const pts = rs => rs.reduce((n, r) => n + r.length / 2, 0);
console.log("states", Object.keys(states).length, "pts", Object.values(states).reduce((n, s) => n + pts(s.rings), 0),
  "| world", Object.keys(world).length, "pts", Object.values(world).reduce((n, r) => n + pts(r), 0),
  "| HK", world["344"] ? pts(world["344"]) : "MISSING", "SG", world["702"] ? pts(world["702"]) : "MISSING",
  "| bytes", out.length, "| fit", proj.scale(), proj.translate());
