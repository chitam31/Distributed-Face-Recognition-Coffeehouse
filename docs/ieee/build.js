// Dựng hai bản báo cáo IEEE:  node docs/ieee/build.js   (cần: npm i -g docx)  ->  docs/BAO_CAO_IEEE_EN.docx, BAO_CAO_IEEE_VI.docx
const path = require("path");
const { build } = require("./lib");
const refs = require("./refs");

(async () => {
  const only = process.argv[2];
  for (const lang of ["en", "vi"]) {
    if (only && only !== lang) continue;
    const content = require(`./content_${lang}`);
    const out = path.join(__dirname, "..", `BAO_CAO_IEEE_${lang.toUpperCase()}.docx`);
    const num = await build(content, refs, out, path.join(__dirname, "figs", lang));
    console.log("ok", out, "| figs:", Object.keys(num.fig).length, "tables:", Object.keys(num.tab).length, "refs:", Object.keys(num.cite).length);
  }
})().catch((e) => { console.error(e); process.exit(1); });
