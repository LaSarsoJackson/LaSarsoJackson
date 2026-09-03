const fs = require('node:fs');
const path = require('node:path');

// Reproducible, schematic cartography. Coordinates do not represent a real place.
const seed = Number(process.argv[2] || 625265036);
const destination = process.argv[3] || path.join(__dirname, '..', 'assets', 'planning-banner.svg');
let state = seed >>> 0;
function random() {
  state = (Math.imul(1664525, state) + 1013904223) >>> 0;
  return state / 4294967296;
}
const f = value => Number(value.toFixed(2));
const elements = [];
const stroke = '#b9c9b7';
const phase = random() * Math.PI * 2;
const centerX = 865 + random() * 125;
const centerY = 75 + random() * 100;

for (let ring = 0; ring < 24; ring++) {
  const points = [];
  for (let k = 0; k <= 180; k++) {
    const angle = k * Math.PI / 90;
    const r = 33 + ring * 17 + 8 * Math.sin(angle * 3 + phase) + 5 * Math.cos(angle * 5 - phase);
    points.push(`${k ? 'L' : 'M'}${f(centerX + Math.cos(angle) * r * 1.33)},${f(centerY + Math.sin(angle) * r * .71)}`);
  }
  elements.push(`<path d="${points.join(' ')}Z" fill="none" stroke="${stroke}" stroke-width="${ring % 5 === 0 ? 1.5 : .8}" opacity="${ring % 5 === 0 ? .43 : .24}"/>`);
}

elements.push('<g transform="translate(122 -127) rotate(19 220 220)">');
for (let row = 0; row < 8; row++) {
  for (let column = 0; column < 9; column++) {
    const x = column * 65;
    const y = row * 55;
    if (random() < .07) continue;
    const accent = random() < .12;
    elements.push(`<rect x="${x}" y="${y}" width="53" height="43" fill="${accent ? '#c6a76e' : '#b9c9b7'}" fill-opacity="${accent ? .22 : .035}" stroke="${accent ? '#c6a76e' : stroke}" stroke-width="1" stroke-opacity="${accent ? .64 : .35}"/>`);
    if (random() > .6) elements.push(`<path d="M${x + 26.5},${y}v43" stroke="${stroke}" stroke-width=".7" opacity=".2"/>`);
  }
}
elements.push('</g>');

const bend = Math.round(20 + random() * 65);
const river = `M620 -50 C${680+bend} 35,${590-bend} 58,640 110 S770 173,665 315`;
elements.push(`<path d="${river}" fill="none" stroke="#142c2a" stroke-width="86"/>`);
elements.push(`<path d="${river}" fill="none" stroke="#8baba8" stroke-width="64"/>`);
elements.push(`<path d="${river}" fill="none" stroke="#c1d0c6" stroke-width="1" opacity=".65"/>`);
elements.push('<path d="M470 146 L794 146" stroke="#142c2a" stroke-width="11"/><path d="M470 146 L794 146" stroke="#d3b27a" stroke-width="3"/>');
elements.push('<circle cx="470" cy="146" r="5" fill="#d3b27a"/><circle cx="794" cy="146" r="5" fill="#d3b27a"/>');
elements.push('<path d="M44 35h24 M56 23v24 M1144 213h24 M1156 201v24" stroke="#c6a76e" opacity=".7" stroke-width="1"/>');

const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="236" viewBox="0 0 1200 236" role="img" aria-labelledby="title desc"><title id="title">Landscape and community</title><desc id="desc">Schematic river, street blocks, and terrain contours. Generated with seed ${seed}; not a geographic dataset.</desc><defs><clipPath id="frame"><rect width="1200" height="236" rx="4"/></clipPath></defs><g clip-path="url(#frame)"><rect width="1200" height="236" fill="#193b35"/>${elements.join('')}<rect x=".5" y=".5" width="1199" height="235" rx="4" fill="none" stroke="#456257"/></g></svg>\n`;
fs.mkdirSync(path.dirname(destination), { recursive: true });
fs.writeFileSync(destination, svg);
console.log(`Generated ${destination} (seed ${seed})`);
