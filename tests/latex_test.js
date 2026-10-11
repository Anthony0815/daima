/* 前端 LaTeX 兜底转换的单元测试（需要 Node.js）。
 *
 * 大模型常输出 LaTeX，而本项目刻意不引入 KaTeX/MathJax，靠 app.js 里的 latexLite()
 * 把常见记号转成纯文本。这个测试直接从 app.js 里抽出该函数来跑，防止改坏。
 *
 * 用法：node tests/latex_test.js
 */

const fs = require('fs');
const path = require('path');

const APP = path.join(__dirname, '..', 'web', 'assets', 'app.js');
const src = fs.readFileSync(APP, 'utf8');

function extract() {
  const start = src.indexOf('const LATEX_SYMBOLS');
  const fnStart = src.indexOf('function latexLite');
  if (start < 0 || fnStart < 0) throw new Error('在 app.js 中找不到 LATEX_SYMBOLS / latexLite');
  const symbols = src.slice(start, fnStart);
  const retIdx = src.indexOf('return out;', fnStart);
  const fnEnd = src.indexOf('\n  }', retIdx) + 4;
  return symbols + src.slice(fnStart, fnEnd);
}

// eslint-disable-next-line no-eval
eval(extract());

const CASES = [
  // 真实模型输出过的形态
  ['\\(\\displaystyle \\lim_{x\\to 0}\\frac{\\sin x-x}{x^3}=-\\frac16\\)',
    'lim(x→0)(sin x-x)/(x^3)=-(1)/(6)'],
  ['\\[\\int_{0}^{1} x^2 dx\\]', '∫(0→1) x^2 dx'],
  ['\\(\\sqrt{1-x^2}\\)', '√(1-x^2)'],
  ['\\boxed{\\lim_{x\\to0}x=0}', 'lim(x→0)x=0'],
  ['\\dfrac{3}{4} + \\tfrac12', '(3)/(4) + (1)/(2)'],
  ['\\left( \\frac{a}{b} \\right)', '( (a)/(b) )'],
  // 简写：\frac16 等价 \frac{1}{6}
  ['\\frac16', '(1)/(6)'],
  ['\\frac{1}{2}x', '(1)/(2)x'],
  // 希腊字母与运算符
  ['\\alpha\\beta\\gamma\\theta\\lambda\\mu\\xi\\pi\\sigma\\varphi\\omega', 'αβγθλμξπσφω'],
  ['\\infty\\cdot\\times\\div\\pm\\leq\\geq\\neq\\approx\\to', '∞·×÷±≤≥≠≈→'],
  ['\\sin\\cos\\tan\\ln\\log\\exp\\max\\min', 'sincostanlnlogexpmaxmin'],
  ['\\partial f / \\partial x', '∂ f / ∂ x'],
  // 上下标要留给后续 math() 处理
  ['\\(e^{3x}\\)', 'e^(3x)'],
  ['\\(x_{n}\\)', 'x_(n)'],
  // 普通文本不得被改动
  ['普通文本 (sin x)/x 完全不变', '普通文本 (sin x)/x 完全不变'],
  ['f_x 与 x^2 保持原样', 'f_x 与 x^2 保持原样'],
  ['《第三章 微分中值定理与导数应用》· 3.2', '《第三章 微分中值定理与导数应用》· 3.2'],
  // 无 LaTeX 时应原样快速返回
  ['没有反斜杠也没有美元符号', '没有反斜杠也没有美元符号']
];

let failed = 0;
for (const [input, want] of CASES) {
  let got;
  try {
    got = latexLite(input);
  } catch (e) {
    got = 'THREW: ' + e.message;
  }
  const ok = got === want;
  if (!ok) failed += 1;
  console.log(
    (ok ? 'PASS ' : 'FAIL ') + JSON.stringify(input) +
    ' -> ' + JSON.stringify(got) + (ok ? '' : '   期望 ' + JSON.stringify(want))
  );
}

console.log('\n' + '='.repeat(60));
console.log('TOTAL %d / %d passed', CASES.length - failed, CASES.length);
console.log('='.repeat(60));
process.exit(failed === 0 ? 0 : 1);
