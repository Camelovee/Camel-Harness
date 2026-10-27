const { readFileSync, writeFileSync } = require("node:fs");
const { join } = require("node:path");
const { Resvg } = require("@resvg/resvg-js");

const assets = join(__dirname, "../assets");
const source = readFileSync(join(assets, "icon.svg"));
const png = (size) => Buffer.from(new Resvg(source, { fitTo: { mode: "width", value: size } }).render().asPng());
writeFileSync(join(assets, "icon.png"), png(1024));

// 直接写入标准 PNG 容器，避免打包时下载额外的图标转换程序。
const records = [["ic07", 128], ["ic08", 256], ["ic09", 512], ["ic10", 1024]].map(([type, size]) => {
  const content = png(size);
  const header = Buffer.alloc(8);
  header.write(type, 0, 4, "ascii");
  header.writeUInt32BE(content.length + 8, 4);
  return Buffer.concat([header, content]);
});
const icnsHeader = Buffer.alloc(8);
icnsHeader.write("icns");
icnsHeader.writeUInt32BE(8 + records.reduce((total, record) => total + record.length, 0), 4);
writeFileSync(join(assets, "icon.icns"), Buffer.concat([icnsHeader, ...records]));

const sizes = [16, 32, 48, 64, 128, 256];
const images = sizes.map(png);
const icoHeader = Buffer.alloc(6 + 16 * sizes.length);
icoHeader.writeUInt16LE(1, 2);
icoHeader.writeUInt16LE(sizes.length, 4);
let offset = icoHeader.length;
sizes.forEach((size, index) => {
  const entry = 6 + 16 * index;
  icoHeader[entry] = icoHeader[entry + 1] = size === 256 ? 0 : size;
  icoHeader.writeUInt16LE(1, entry + 4);
  icoHeader.writeUInt16LE(32, entry + 6);
  icoHeader.writeUInt32LE(images[index].length, entry + 8);
  icoHeader.writeUInt32LE(offset, entry + 12);
  offset += images[index].length;
});
writeFileSync(join(assets, "icon.ico"), Buffer.concat([icoHeader, ...images]));
