// Xuất cùng nội dung (content_en.js / content_vi.js) sang LaTeX dùng lớp IEEEtran chính thức rồi biên dịch bằng XeLaTeX.
//   node docs/ieee/tex.js [en|vi]   ->  docs/latex/BAO_CAO_IEEE_EN.pdf, BAO_CAO_IEEE_VI.pdf
// Một nguồn nội dung duy nhất cho cả .docx (build.js) và .tex (tex.js), nên hai bản không thể lệch nhau.
const fs = require("fs");
const path = require("path");
const cp = require("child_process");
const { numbering, nb } = require("./lib");
const refs = require("./refs");

const OUT = path.join(__dirname, "..", "latex");
const SYM = { "τ": "$\\tau$", "λ": "$\\lambda$", "≤": "$\\leq$", "≥": "$\\geq$", "≈": "$\\approx$", "×": "$\\times$",
  "→": "$\\rightarrow$", "−": "$-$", "<": "$<$", ">": "$>$", "±": "$\\pm$" };

function plainEsc(t) {
  let o = "";
  for (const ch of t) {
    if (SYM[ch]) o += SYM[ch];
    else if (ch === "\u00A0") o += "~";
    else if (ch === "\\") o += "\\textbackslash{}";
    else if ("{}#$%&_".includes(ch)) o += "\\" + ch;
    else if (ch === "~") o += "\\textasciitilde{}";
    else if (ch === "^") o += "\\textasciicircum{}";
    else o += ch;
  }
  return o;
}

function plain(t) {
  // địa chỉ URL -> \url{}
  return nb(t).split(/(https?:\/\/[^\s)]*[^\s).,])/).map((seg, i) => (i % 2 ? `\\url{${seg}}` : plainEsc(seg))).join("");
}

function code(t) {
  let o = "";
  for (const ch of t) {
    if (ch === "\\") o += "\\textbackslash{}";
    else if ("{}#$%&".includes(ch)) o += "\\" + ch;
    else if (ch === "_") o += "\\_\\allowbreak{}";
    else if (ch === "~") o += "\\textasciitilde{}";
    else if (ch === "^") o += "\\textasciicircum{}";
    else if (".,/".includes(ch)) o += ch + "\\allowbreak{}";
    else o += ch;
  }
  return o;
}

const TOKEN = /(\*\*(?:[^*]|\*(?!\*))+\*\*|\*[^*\s](?:[^*]*[^*\s])?\*|`[^`]+`|\[\[[\s\S]+?\]\]|\{(?:fig|tab|cite|eq):[^}]+\}|\^\{[^}]+\}|_\{[^}]+\})/g;
const MATHVAR = /^([A-Za-z]|τ|λ|λW)$/;

function toTex(text, ctx) {
  const out = [];
  let last = 0, m;
  const re = new RegExp(TOKEN.source, "g");
  const push = (s) => out.push(s);
  while ((m = re.exec(text))) {
    if (m.index > last) push(plain(text.slice(last, m.index)));
    const tok = m[0];
    if (tok.startsWith("**")) push(`\\textbf{${toTex(tok.slice(2, -2), ctx)}}`);
    else if (tok.startsWith("*")) {
      const inner = tok.slice(1, -1);
      push(MATHVAR.test(inner) ? `$${inner.replace(/τ/g, "\\tau ").replace(/λ/g, "\\lambda ").trim()}$` : `\\textit{${toTex(inner, ctx)}}`);
    } else if (tok[0] === "`") push(`\\texttt{${code(tok.slice(1, -1))}}`);
    else if (tok.startsWith("[[")) push(toTex(tok.slice(2, -2), ctx));
    else if (tok.startsWith("^{")) push(`\\textsuperscript{${toTex(tok.slice(2, -1), ctx)}}`);
    else if (tok.startsWith("_{")) push(`\\textsubscript{${toTex(tok.slice(2, -1), ctx)}}`);
    else {
      const kind = tok.slice(1, tok.indexOf(":")), arg = tok.slice(tok.indexOf(":") + 1, -1);
      if (kind === "fig") push(`${ctx.L.figIn}~\\ref{fig:${arg}}`);
      else if (kind === "tab") push(`${ctx.L.tabIn}~\\ref{tab:${arg}}`);
      else if (kind === "eq") push(`(\\ref{eq:${arg}})`);
      else if (kind === "cite") {
        if (out.length && /\s$/.test(out[out.length - 1])) out[out.length - 1] = out[out.length - 1].replace(/\s+$/, "");
        push(`~\\cite{${arg}}`);
      }
    }
    last = re.lastIndex;
  }
  if (last < text.length) push(plain(text.slice(last)));
  return out.join("");
}

// Hậu xử lý ký hiệu toán: gộp các mảnh $..$ liền nhau (W = L/λ), đưa số vào công thức (τ = 0.46) và dùng {,} cho dấu phẩy thập phân.
function mathPost(s) {
  let prev;
  do {
    prev = s;
    s = s.replace(/\$([^$]+)\$( = |\/)\$([^$]+)\$/g, (_, a, op, b) => `$${a}${op}${b}$`);
  } while (s !== prev);
  s = s.replace(/\$([^$]+)\$ = (\d+(?:[.,]\d+)*(?:, \d+(?:[.,]\d+)*)*)/g,
    (_, a, n) => `$${a} = ${n.replace(/(\d),(\d)/g, "$1{,}$2")}$`);
  return s;
}
const T = (text, ctx) => mathPost(toTex(text, ctx));

function renderTable(b, ctx) {
  const wide = false;                          // bảng chịu lỗi để trong một cột: table* trôi sang trang cuối, để lại khoảng trống
  const base = wide ? "\\textwidth" : "\\columnwidth";
  const spec = b.align.map((a, i) =>
    (a === "c" ? ">{\\centering\\arraybackslash}" : ">{\\raggedright\\arraybackslash}") +
    `p{\\dimexpr ${b.widths[i].toFixed(3)}${base}-6pt\\relax}`).join("");
  const env = wide ? "table*" : "table";
  const head = b.head.map((h) => `\\textbf{${T(h, ctx)}}`).join(" & ");
  const rows = b.rows.map((r) => r.map((c) => T(String(c), ctx)).join(" & ") + " \\\\").join("\n");
  return `\\begin{${env}}[!t]
\\caption{${T(b.caption, ctx)}}
\\label{tab:${b.id}}
\\centering
{\\footnotesize\\setlength{\\tabcolsep}{3pt}\\renewcommand{\\arraystretch}{1.12}
\\begin{tabular}{${spec}}
\\toprule
${head} \\\\
\\midrule
${rows}
\\bottomrule
\\end{tabular}}
\\end{${env}}
`;
}

function renderBlocks(content, ctx, lang) {
  const out = [];
  for (const b of content.blocks) {
    switch (b.k) {
      case "h1": out.push(`\\section{${T(b.t, ctx)}}`); break;
      case "h2": out.push(`\\subsection{${T(b.t, ctx)}}`); break;
      case "h3": out.push(`\\subsubsection{${T(b.t, ctx)}}`); break;
      case "p": out.push(T(b.t, ctx)); break;
      case "ul": out.push(`\\begin{itemize}\n${b.items.map((i) => "\\item " + T(i, ctx)).join("\n")}\n\\end{itemize}`); break;
      case "ol": out.push(`\\begin{enumerate}\n${b.items.map((i) => "\\item " + T(i, ctx)).join("\n")}\n\\end{enumerate}`); break;
      case "code": out.push(`{\\footnotesize\n\\begin{verbatim}\n${b.t}\n\\end{verbatim}}`); break;
      case "eq":
        out.push(`\\begin{equation}\n\\label{eq:${b.id}}\n${b.t.replace(/\*/g, "").replace(/−/g, "-")}\n\\end{equation}`);
        break;
      case "fig":
        out.push(`\\begin{figure}[!t]
\\centering
\\includegraphics[width=${(b.widthIn / 3.5).toFixed(3)}\\columnwidth]{figs/${lang}/${b.file}}
\\caption{${T(b.caption, ctx)}}
\\label{fig:${b.id}}
\\end{figure}`);
        break;
      case "tab": out.push(renderTable(b, ctx)); break;
      default: throw new Error("khối không rõ: " + b.k);
    }
  }
  return out.join("\n\n");
}

function document(lang, content) {
  const L = content.labels;
  const num = numbering(content, refs);
  const ctx = { L };
  const m = content.meta;
  const vi = lang === "vi";
  const authors = m.authors.map((a) =>
    `\\IEEEauthorblockN{${plain(a.name)}}\n\\IEEEauthorblockA{\\textit{${plain(m.idLabel)}~${a.id}}\\\\\n\\textit{${plain(m.courseLine)}}}`).join("\n\\and\n");
  const bib = Object.entries(num.cite).sort((a, b) => a[1] - b[1])
    .map(([key]) => `\\bibitem{${key}} ${T(refs[key], ctx)}`).join("\n\n");
  const names = vi
    ? `\\renewcommand{\\abstractname}{${L.abstract}}\n\\renewcommand{\\IEEEkeywordsname}{${L.keywords}}\n\\renewcommand{\\refname}{${L.references}}\n\\renewcommand{\\figurename}{${L.figCap}}\n\\renewcommand{\\tablename}{${L.tabCap}}\n` +
      `\\hyphenpenalty=10000 \\exhyphenpenalty=10000 \\tolerance=2000 \\emergencystretch=2em`
    : "";
  return `% Tệp này được SINH TỰ ĐỘNG bởi docs/ieee/tex.js từ content_${lang}.js; hãy sửa nội dung ở đó rồi chạy lại.
\\documentclass[conference,a4paper]{IEEEtran}
\\usepackage{fontspec}
\\setmainfont{TeX Gyre Termes}
\\setmonofont[Scale=0.85]{TeX Gyre Cursor}
\\usepackage{unicode-math}
\\setmathfont{Latin Modern Math}
\\usepackage{microtype}
\\hyphenation{postgresql pgvector rabbitmq insightface mysql arcface scrfd zerotier tkinter}
\\usepackage{graphicx}
\\usepackage{booktabs}
\\usepackage{array}
\\usepackage{cite}
\\usepackage{url}
\\urlstyle{same}
\\renewcommand{\\labelenumi}{\\arabic{enumi})}
\\usepackage[hidelinks,pdftitle={${m.title.replace(/[{}\\#%&_$~^]/g, "")}},pdfauthor={${m.authors.map((a) => a.name).join(", ")}},pdflang={${L.lang}}]{hyperref}
${names}
\\begin{document}
\\title{${plain(m.title)}}
\\author{
${authors}
}
\\maketitle
\\thispagestyle{plain}
\\pagestyle{plain}
\\begin{center}\\vspace{-1.2ex}\\textit{${plain(m.note)}}\\end{center}
\\begin{abstract}
${T(content.abstract, ctx)}
\\end{abstract}
\\begin{IEEEkeywords}
${T(content.keywords, ctx)}
\\end{IEEEkeywords}

${renderBlocks(content, ctx, lang)}

\\begin{thebibliography}{${Object.keys(num.cite).length}}
${bib}
\\end{thebibliography}
\\end{document}
`;
}

function run(lang) {
  fs.mkdirSync(path.join(OUT, "figs", lang), { recursive: true });
  for (const f of fs.readdirSync(path.join(__dirname, "figs", lang)))
    fs.copyFileSync(path.join(__dirname, "figs", lang, f), path.join(OUT, "figs", lang, f));
  const content = require(`./content_${lang}`);
  const base = `BAO_CAO_IEEE_${lang.toUpperCase()}`;
  fs.writeFileSync(path.join(OUT, base + ".tex"), document(lang, content));
  let log = "";
  for (let pass = 1; pass <= 2; pass++) {
    const r = cp.spawnSync("xelatex", ["-interaction=nonstopmode", "-halt-on-error", base + ".tex"], { cwd: OUT, encoding: "utf8" });
    log = r.stdout + r.stderr;
    if (r.status !== 0) { console.error(log.split("\n").slice(-40).join("\n")); throw new Error("xelatex lỗi (" + lang + ")"); }
  }
  const lg = fs.readFileSync(path.join(OUT, base + ".log"), "utf8");
  const warn = (re) => (lg.match(re) || []).length;
  console.log("ok", base + ".pdf",
    "| pages:", (lg.match(/Output written on .*\((\d+) pages/) || [])[1],
    "| overfull:", warn(/Overfull \\[hv]box/g), "| underfull:", warn(/Underfull \\[hv]box/g),
    "| missing glyph:", warn(/Missing character/g), "| undefined refs:", warn(/undefined/g));
}

const only = process.argv[2];
for (const lang of ["en", "vi"]) if (!only || only === lang) run(lang);
