// Bộ dựng báo cáo chuẩn IEEE (hai cột) bằng docx-js. Nội dung nằm ở content_en.js / content_vi.js.
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, ImageRun, Table, TableRow, TableCell, WidthType, AlignmentType,
  BorderStyle, ShadingType, LevelFormat, LevelSuffix, HeadingLevel, TabStopType, SectionType, Footer, PageNumber,
  VerticalAlign, LineRuleType,
} = require("docx");

const FONT = "Times New Roman";
const COL = 5040;                       // bề rộng một cột = 3,5 inch (DXA)
const PAGE = { width: 11906, height: 16838 };   // A4
const MARGIN = { top: 1077, bottom: 1417, left: 733, right: 733 };
const GAP = 360;
const FULL = PAGE.width - MARGIN.left - MARGIN.right;

// ------------------------------------------------------------------ DSL khối nội dung
const H1 = (t) => ({ k: "h1", t });
const H2 = (t) => ({ k: "h2", t });
const H3 = (t) => ({ k: "h3", t });
const P = (t) => ({ k: "p", t });
const UL = (...items) => ({ k: "ul", items });
const OL = (...items) => ({ k: "ol", items });
const CODE = (t) => ({ k: "code", t });
const EQ = (id, t) => ({ k: "eq", id, t });
const FIG = (id, file, caption, widthIn = 3.45) => ({ k: "fig", id, file, caption, widthIn });
const TAB = (id, caption, widths, align, head, rows, opts = {}) => ({ k: "tab", id, caption, widths, align, head, rows, ...opts });

// ------------------------------------------------------------------ đánh số tham chiếu
const ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"];

function collectText(b) {
  switch (b.k) {
    case "h1": case "h2": case "h3": case "p": case "eq": return [b.t];
    case "ul": case "ol": return b.items;
    case "fig": return [b.caption];
    case "tab": return [b.caption, ...b.head, ...b.rows.flat()];
    default: return [];
  }
}

function numbering(content, refs) {
  const fig = {}, tab = {}, eq = {}, cite = {};
  let nf = 0, nt = 0, ne = 0, nc = 0;
  for (const b of content.blocks) {
    if (b.k === "fig") fig[b.id] = ++nf;
    if (b.k === "tab") tab[b.id] = ROMAN[++nt];
    if (b.k === "eq") eq[b.id] = ++ne;
  }
  const re = /\{cite:([^}]+)\}/g;
  const scan = (txt) => {
    let m;
    while ((m = re.exec(txt))) for (const key of m[1].split(",")) if (!(key in cite)) cite[key] = ++nc;
  };
  for (const b of content.blocks) collectText(b).forEach(scan);
  for (const key of Object.keys(cite)) if (!refs[key]) throw new Error("thiếu tài liệu tham khảo: " + key);
  return { fig, tab, eq, cite };
}

// ------------------------------------------------------------------ chuỗi văn bản có định dạng
function mk(text, st) {
  const o = { text, font: st.font || FONT, size: st.size };
  if (st.bold) o.bold = true;
  if (st.italics) o.italics = true;
  if (st.smallCaps) o.smallCaps = true;
  if (st.highlight) o.highlight = st.highlight;
  if (st.sup) o.superScript = true;
  if (st.sub) o.subScript = true;
  return new TextRun(o);
}

const TOKEN = /(\*\*(?:[^*]|\*(?!\*))+\*\*|\*[^*\s](?:[^*]*[^*\s])?\*|`[^`]+`|\[\[[\s\S]+?\]\]|\{(?:fig|tab|cite|eq):[^}]+\}|\^\{[^}]+\}|_\{[^}]+\})/g;

// Dấu cách không ngắt dòng theo thông lệ dàn trang LaTeX (dấu ~): số + đơn vị, "Fig. 1", "Section V", "vol. 9",
// chữ cái đầu tên người, và trước các ký hiệu quan hệ (=, ≤, ≈ ...) để không bị tách dòng ở đầu.
const NBSP = " ";
function nb(t) {
  return t
    .replace(/(\d) (ms|s|px|GHz|GiB|KB|kB|MB|req\/s)(?![\w/])/g, "$1" + NBSP + "$2")
    .replace(/(\d) ([×x]) (\d)/g, "$1" + NBSP + "$2" + NBSP + "$3")
    .replace(/\b(Sections?|Mục|Fig\.|Table|Hình|Bảng|vol\.|no\.|pp\.) (?=[IVX\d])/g, "$1" + NBSP)
    .replace(/\b([A-Z]\.) (?=[A-Z])/g, "$1" + NBSP)
    .replace(/(\S) (=|≤|≥|≈|<|>) /g, "$1" + NBSP + "$2 ");
}

function inline(text, ctx, st) {
  const out = [];
  let last = 0, m;
  TOKEN.lastIndex = 0;
  const re = new RegExp(TOKEN.source, "g");
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(mk(nb(text.slice(last, m.index)), st));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(...inline(tok.slice(2, -2), ctx, { ...st, bold: true }));
    else if (tok.startsWith("*")) out.push(...inline(tok.slice(1, -1), ctx, { ...st, italics: true }));
    else if (tok[0] === "`") out.push(mk(tok.slice(1, -1), { ...st, font: "Courier New", size: Math.round(st.size * 0.85) }));
    else if (tok.startsWith("[[")) out.push(...inline(tok.slice(2, -2), ctx, { ...st, highlight: "yellow" }));
    else if (tok.startsWith("^{")) out.push(...inline(tok.slice(2, -1), ctx, { ...st, sup: true }));
    else if (tok.startsWith("_{")) out.push(...inline(tok.slice(2, -1), ctx, { ...st, sub: true }));
    else {
      const [kind, arg] = [tok.slice(1, tok.indexOf(":")), tok.slice(tok.indexOf(":") + 1, -1)];
      if (kind === "fig") out.push(mk(`${ctx.L.figIn} ${ctx.num.fig[arg]}`, st));
      else if (kind === "tab") out.push(mk(`${ctx.L.tabIn} ${ctx.num.tab[arg]}`, st));
      else if (kind === "eq") out.push(mk(`(${ctx.num.eq[arg]})`, st));
      else if (kind === "cite") out.push(mk(arg.split(",").map((k) => `[${ctx.num.cite[k]}]`).join(", "), st));
    }
    last = re.lastIndex;
  }
  if (last < text.length) out.push(mk(nb(text.slice(last)), st));
  return out;
}

// ------------------------------------------------------------------ khối -> phần tử docx
function pngSize(file) {
  const b = fs.readFileSync(file);
  return { w: b.readUInt32BE(16), h: b.readUInt32BE(20) };
}

const NONE = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
const RULE = (sz) => ({ style: BorderStyle.SINGLE, size: sz, color: "000000" });

function renderTable(b, ctx) {
  const widths = b.widths.map((f) => Math.round(f * COL));
  widths[widths.length - 1] += COL - widths.reduce((a, c) => a + c, 0);
  const al = { l: AlignmentType.LEFT, c: AlignmentType.CENTER, r: AlignmentType.RIGHT };
  const sz = b.size || 16;
  const mkRow = (cells, kind, last) => new TableRow({
    cantSplit: true,
    tableHeader: kind === "head",
    children: cells.map((txt, i) => new TableCell({
      width: { size: widths[i], type: WidthType.DXA },
      verticalAlign: VerticalAlign.TOP,
      margins: { top: 20, bottom: 20, left: 50, right: 50 },
      borders: {
        top: kind === "head" ? RULE(8) : NONE,
        bottom: kind === "head" ? RULE(4) : last ? RULE(8) : NONE,
        left: NONE, right: NONE,
      },
      children: [new Paragraph({
        style: "TableText",
        alignment: kind === "head" ? (b.align[i] === "l" ? AlignmentType.LEFT : AlignmentType.CENTER) : al[b.align[i]],
        keepNext: !last,
        children: inline(String(txt), ctx, { size: sz, bold: kind === "head" }),
      })],
    })),
  });
  const rows = [mkRow(b.head, "head", false), ...b.rows.map((r, i) => mkRow(r, "body", i === b.rows.length - 1))];
  const cap1 = new Paragraph({
    style: "TableCap", keepNext: true,
    children: [mk(`${ctx.L.tabCap} ${ctx.num.tab[b.id]}`, { size: 16 })],
  });
  const cap2 = new Paragraph({
    style: "TableCap", keepNext: true, spacing: { before: 0, after: 60 },
    children: inline(b.caption, ctx, { size: 16, smallCaps: true }),
  });
  const table = new Table({
    width: { size: COL, type: WidthType.DXA },
    columnWidths: widths,
    borders: { top: NONE, bottom: NONE, left: NONE, right: NONE, insideHorizontal: NONE, insideVertical: NONE },
    rows,
  });
  return [cap1, cap2, table, new Paragraph({ spacing: { before: 0, after: 100 }, children: [] })];
}

function renderBlocks(content, ctx, olCount) {
  const out = [];
  let ol = 0;
  for (const b of content.blocks) {
    switch (b.k) {
      case "h1":
        out.push(new Paragraph({ heading: HeadingLevel.HEADING_1, numbering: { reference: "heads", level: 0 }, children: inline(b.t, ctx, { size: 20, smallCaps: true }) }));
        break;
      case "h2":
        out.push(new Paragraph({ heading: HeadingLevel.HEADING_2, numbering: { reference: "heads", level: 1 }, children: inline(b.t, ctx, { size: 20, italics: true }) }));
        break;
      case "h3":
        out.push(new Paragraph({ heading: HeadingLevel.HEADING_3, numbering: { reference: "heads", level: 2 }, children: inline(b.t, ctx, { size: 20, italics: true }) }));
        break;
      case "p":
        out.push(new Paragraph({ style: "Body", children: inline(b.t, ctx, { size: 20 }) }));
        break;
      case "ul":
        for (const it of b.items) out.push(new Paragraph({ style: "Item", numbering: { reference: "bul", level: 0 }, children: inline(it, ctx, { size: 20 }) }));
        break;
      case "ol": {
        const ref = "ol" + (ol++);
        for (const it of b.items) out.push(new Paragraph({ style: "Item", numbering: { reference: ref, level: 0 }, children: inline(it, ctx, { size: 20 }) }));
        break;
      }
      case "code":
        b.t.split("\n").forEach((line, i, arr) => out.push(new Paragraph({
          style: "Code", keepLines: true, keepNext: i < arr.length - 1,
          children: [mk(line === "" ? " " : line, { font: "Courier New", size: 15 })],
        })));
        out.push(new Paragraph({ spacing: { before: 0, after: 60 }, children: [] }));
        break;
      case "eq":
        out.push(new Paragraph({
          style: "Eq",
          tabStops: [{ type: TabStopType.CENTER, position: COL / 2 }, { type: TabStopType.RIGHT, position: COL }],
          children: [new TextRun({ text: "\t", font: FONT, size: 20 }), ...inline(b.t, ctx, { size: 20 }),
            new TextRun({ text: `\t(${ctx.num.eq[b.id]})`, font: FONT, size: 20 })],
        }));
        break;
      case "fig": {
        const file = path.join(ctx.figDir, b.file);
        const { w, h } = pngSize(file);
        const wpx = Math.round(b.widthIn * 96), hpx = Math.round((wpx * h) / w);
        out.push(new Paragraph({
          style: "Fig", keepNext: true,
          children: [new ImageRun({ type: "png", data: fs.readFileSync(file), transformation: { width: wpx, height: hpx },
            altText: { title: `${ctx.L.figCap} ${ctx.num.fig[b.id]}`, description: b.caption.replace(/\{(fig|tab|eq|cite):([^}]+)\}/g, (m, k, a) => k === "fig" ? `${ctx.L.figIn} ${ctx.num.fig[a]}` : k === "tab" ? `${ctx.L.tabIn} ${ctx.num.tab[a]}` : k === "eq" ? `(${ctx.num.eq[a]})` : a.split(",").map((c) => `[${ctx.num.cite[c]}]`).join(", ")).replace(/[*`\[\]{}]/g, ""), name: b.file } })],
        }));
        const plain = b.caption.length < 70;
        out.push(new Paragraph({
          style: "FigCap", alignment: plain ? AlignmentType.CENTER : AlignmentType.JUSTIFIED,
          children: [mk(`${ctx.L.figCap} ${ctx.num.fig[b.id]}. `, { size: 16 }), ...inline(b.caption, ctx, { size: 16 })],
        }));
        break;
      }
      case "tab":
        out.push(...renderTable(b, ctx));
        break;
      default: throw new Error("khối không rõ: " + b.k);
    }
  }
  return out;
}

// Chỉnh nhỏ XML cho đúng lược đồ OOXML (Word khó tính hơn LibreOffice): bỏ <w:highlightCs/> (không thuộc lược đồ)
// và đặt lại thứ tự phần tử trong settings.xml.
async function tidy(buf) {
  const JSZip = require(require.resolve("jszip", { paths: [path.dirname(require.resolve("docx"))] }));
  const zip = await JSZip.loadAsync(buf);
  let doc = await zip.file("word/document.xml").async("string");
  doc = doc.replace(/<w:highlightCs[^>]*\/>/g, "");
  zip.file("word/document.xml", doc);
  let st = await zip.file("word/settings.xml").async("string");
  const m = st.match(/<w:evenAndOddHeaders[^>]*\/>/);
  if (m) {
    st = st.replace(m[0], "");
    st = st.replace(/(<w:doNotHyphenateCaps[^>]*\/>)/, "$1" + m[0]);
    if (!st.includes(m[0])) st = st.replace(/(<w:compat>)/, m[0] + "$1");
  }
  zip.file("word/settings.xml", st);
  return zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" });
}

// ------------------------------------------------------------------ tài liệu hoàn chỉnh
async function build(content, refs, outFile, figDir) {
  const L = content.labels;
  const num = numbering(content, refs);
  const ctx = { L, num, figDir };

  const body = renderBlocks(content, ctx);
  // tài liệu tham khảo
  const refParas = [new Paragraph({ heading: HeadingLevel.HEADING_1, children: inline(L.references, ctx, { size: 20, smallCaps: true }) })];
  Object.entries(num.cite).sort((a, b) => a[1] - b[1]).forEach(([key, n]) => {
    refParas.push(new Paragraph({
      style: "Ref",
      children: [mk(`[${n}]\t`, { size: 16 }), ...inline(refs[key], ctx, { size: 16 })],
    }));
  });

  // khối tiêu đề (1 cột)
  const m = content.meta;
  // nhiều tác giả: bảng không viền, mỗi tác giả một cột (họ tên / mã số / môn học), rồi một dòng ghi chú ở giữa
  const authorBlock = () => {
    const n = m.authors.length;
    const widths = m.authors.map(() => Math.floor(FULL / n));
    widths[n - 1] += FULL - widths.reduce((a, c) => a + c, 0);
    const line = (txt, size, italics, after = 0) => new Paragraph({
      style: "Affil", spacing: { before: 0, after }, children: inline(txt, ctx, { size, italics }),
    });
    const cells = m.authors.map((a, i) => new TableCell({
      width: { size: widths[i], type: WidthType.DXA },
      verticalAlign: VerticalAlign.TOP,
      margins: { top: 0, bottom: 0, left: 60, right: 60 },
      borders: { top: NONE, bottom: NONE, left: NONE, right: NONE },
      children: [
        new Paragraph({ style: "Author", spacing: { before: 0, after: 0 }, children: inline(a.name, ctx, { size: 24 }) }),
        line(`${m.idLabel} ${a.id}`, 20, true),
        line(m.courseLine, 20, true),
      ],
    }));
    return [
      new Table({
        width: { size: FULL, type: WidthType.DXA },
        columnWidths: widths,
        borders: { top: NONE, bottom: NONE, left: NONE, right: NONE, insideHorizontal: NONE, insideVertical: NONE },
        rows: [new TableRow({ cantSplit: true, children: cells })],
      }),
      new Paragraph({ style: "Affil", spacing: { before: 80, after: 200 }, children: inline(m.note, ctx, { size: 20, italics: true }) }),
    ];
  };
  const authorParas = m.authors ? authorBlock() : [
    new Paragraph({ style: "Author", spacing: { before: 60, after: 0 }, children: inline(m.author, ctx, { size: 24 }) }),
    ...m.affil.map((a, i) => new Paragraph({ style: "Affil", spacing: { before: 0, after: i === m.affil.length - 1 ? 200 : 0 }, children: inline(a, ctx, { size: 20, italics: i === 0 }) })),
  ];
  const title = [
    new Paragraph({ style: "PaperTitle", children: [mk(m.title, { size: 44 })] }),
    ...authorParas,
  ];

  const abstractP = new Paragraph({
    style: "Abstract",
    children: [mk(`${L.abstract}—`, { size: 18, bold: true, italics: true }), ...inline(content.abstract, ctx, { size: 18, bold: true })],
  });
  const kwP = new Paragraph({
    style: "Abstract", spacing: { before: 0, after: 120 },
    children: [mk(`${L.keywords}—`, { size: 18, bold: true, italics: true }), ...inline(content.keywords, ctx, { size: 18, bold: true })],
  });

  const footer = new Footer({
    children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 16 })] })],
  });
  const pageProps = { size: { width: PAGE.width, height: PAGE.height }, margin: { ...MARGIN, footer: 567, header: 567 } };

  const olCount = content.blocks.filter((b) => b.k === "ol").length;
  const olConfigs = Array.from({ length: olCount }, (_, i) => ({
    reference: "ol" + i,
    levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1)", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 300, hanging: 300 } } } }],
  }));

  const doc = new Document({
    title: m.title,
    creator: "",
    description: content.docDescription || "",
    hyphenation: { autoHyphenation: true, doNotHyphenateCaps: true },
    styles: {
      default: { document: { run: { font: FONT, size: 20, language: { value: L.lang } } } },
      paragraphStyles: [
        { id: "Body", name: "Body", basedOn: "Normal", quickFormat: true,
          paragraph: { alignment: AlignmentType.JUSTIFIED, spacing: { before: 0, after: 60, line: 240, lineRule: LineRuleType.AUTO }, indent: { firstLine: 202 } } },
        { id: "Item", name: "Item", basedOn: "Normal",
          paragraph: { alignment: AlignmentType.JUSTIFIED, spacing: { before: 0, after: 40, line: 240, lineRule: LineRuleType.AUTO } } },
        { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Body", quickFormat: true,
          run: { size: 20, smallCaps: true, font: FONT },
          paragraph: { alignment: AlignmentType.CENTER, spacing: { before: 200, after: 80 }, keepNext: true, outlineLevel: 0 } },
        { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Body", quickFormat: true,
          run: { size: 20, italics: true, font: FONT },
          paragraph: { alignment: AlignmentType.LEFT, spacing: { before: 120, after: 60 }, keepNext: true, outlineLevel: 1 } },
        { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Body", quickFormat: true,
          run: { size: 20, italics: true, font: FONT },
          paragraph: { alignment: AlignmentType.LEFT, spacing: { before: 80, after: 40 }, keepNext: true, outlineLevel: 2 } },
        { id: "PaperTitle", name: "Paper Title", basedOn: "Normal",
          paragraph: { alignment: AlignmentType.CENTER, spacing: { before: 0, after: 160, line: 240, lineRule: LineRuleType.AUTO } } },
        { id: "Author", name: "Author", basedOn: "Normal", paragraph: { alignment: AlignmentType.CENTER } },
        { id: "Affil", name: "Affiliation", basedOn: "Normal", paragraph: { alignment: AlignmentType.CENTER } },
        { id: "Abstract", name: "Abstract", basedOn: "Normal",
          paragraph: { alignment: AlignmentType.JUSTIFIED, spacing: { before: 0, after: 60, line: 240, lineRule: LineRuleType.AUTO }, indent: { firstLine: 202 } } },
        { id: "TableCap", name: "Table Caption", basedOn: "Normal",
          run: { size: 16, smallCaps: true },
          paragraph: { alignment: AlignmentType.CENTER, spacing: { before: 100, after: 0 } } },
        { id: "TableText", name: "Table Text", basedOn: "Normal",
          run: { size: 16 }, paragraph: { spacing: { before: 0, after: 0, line: 228, lineRule: LineRuleType.AUTO } } },
        { id: "Fig", name: "Figure", basedOn: "Normal",
          paragraph: { alignment: AlignmentType.CENTER, spacing: { before: 80, after: 20 } } },
        { id: "FigCap", name: "Figure Caption", basedOn: "Normal",
          run: { size: 16 }, paragraph: { spacing: { before: 0, after: 140, line: 228, lineRule: LineRuleType.AUTO } } },
        { id: "Code", name: "Code", basedOn: "Normal",
          run: { font: "Courier New", size: 15 },
          paragraph: { alignment: AlignmentType.LEFT, spacing: { before: 0, after: 0, line: 220, lineRule: LineRuleType.AUTO },
            indent: { left: 60, right: 60 }, shading: { type: ShadingType.CLEAR, fill: "F2F2F2", color: "auto" } } },
        { id: "Eq", name: "Equation", basedOn: "Normal", paragraph: { spacing: { before: 40, after: 80 } } },
        { id: "Ref", name: "Reference", basedOn: "Normal",
          run: { size: 16 },
          paragraph: { alignment: AlignmentType.LEFT, spacing: { before: 0, after: 20, line: 228, lineRule: LineRuleType.AUTO },
            indent: { left: 340, hanging: 340 }, tabStops: [{ type: TabStopType.LEFT, position: 340 }] } },
      ],
    },
    numbering: {
      config: [
        { reference: "heads", levels: [
          { level: 0, format: LevelFormat.UPPER_ROMAN, text: "%1.", alignment: AlignmentType.LEFT, suffix: LevelSuffix.SPACE,
            style: { run: { smallCaps: true, size: 20 }, paragraph: { indent: { left: 0, firstLine: 0 } } } },
          { level: 1, format: LevelFormat.UPPER_LETTER, text: "%2.", alignment: AlignmentType.LEFT, suffix: LevelSuffix.SPACE,
            style: { run: { italics: true, size: 20 }, paragraph: { indent: { left: 0, firstLine: 0 } } } },
          { level: 2, format: LevelFormat.DECIMAL, text: "%3)", alignment: AlignmentType.LEFT, suffix: LevelSuffix.SPACE,
            style: { run: { italics: true, size: 20 }, paragraph: { indent: { left: 0, firstLine: 0 } } } },
        ] },
        { reference: "bul", levels: [
          { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 260, hanging: 200 } } } } ] },
        ...olConfigs,
      ],
    },
    sections: [
      {
        properties: { page: pageProps, column: { count: 1 } },
        footers: { default: footer },
        children: title,
      },
      {
        properties: { type: SectionType.CONTINUOUS, page: pageProps, column: { count: 2, space: GAP, equalWidth: true } },
        footers: { default: footer },
        children: [abstractP, kwP, ...body, ...refParas],
      },
    ],
  });
  const buf = await Packer.toBuffer(doc);
  fs.writeFileSync(outFile, await tidy(buf));
  return num;
}

module.exports = { build, numbering, nb, H1, H2, H3, P, UL, OL, CODE, EQ, FIG, TAB };
