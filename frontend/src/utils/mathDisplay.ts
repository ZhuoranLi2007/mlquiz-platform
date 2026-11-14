/** Add math delimiters only to known plain-text formulas in imported material. */
const MATH_SEGMENTS = /(\$\$[\s\S]*?\$\$|\$[^$]*?\$)/g

function outsideMath(source: string, pattern: RegExp, replacement: string | ((...args: any[]) => string)) {
  return source.split(MATH_SEGMENTS).map((part, index) =>
    index % 2 ? part : part.replace(pattern, replacement as string)).join('')
}

export function normalizeDisplayMath(source: string): string {
  let text = source
  text = outsideMath(text,
    /\b(J\s*=\s*)?w\^T\s*S_b\s*w\s*\/\s*\(?\s*w\^T\s*S_w\s*w\s*\)?/g,
    (_match: string, j: string) => `$${j ? 'J=' : ''}\\frac{w^{\\mathsf T}S_b w}{w^{\\mathsf T}S_w w}$`)
  text = outsideMath(text, /\bw\^T\s*S_w\s*w\s*=\s*1\b/g,
    '$w^{\\mathsf T}S_w w=1$')
  text = outsideMath(text, /\bS_b\s*w\s*=\s*λ\s*S_w\s*w\b/g,
    '$S_b w=\\lambda S_w w$')
  text = outsideMath(text, /\bS_w\^\{-1\}\s*\(\s*μ1\s*-\s*μ2\s*\)/g,
    '$S_w^{-1}(\\mu_1-\\mu_2)$')
  text = outsideMath(text, /\b(g|σ)\(z\)\s*=\s*1\s*\/\s*\(1\s*\+\s*e\^\s*(?:\(\s*[-−]z\s*\)|\{\s*[-−]z\s*\})\)/g,
    (_match: string, name: string) => `$${name === 'σ' ? '\\sigma' : 'g'}(z)=\\frac{1}{1+e^{-z}}$`)
  text = outsideMath(text, /σ\(2\)\s*=\s*1\s*\/\s*\(1\s*\+\s*e\^\(\s*[−-]2\s*\)\)/g,
    '$\\sigma(2)=\\frac{1}{1+e^{-2}}$')
  text = outsideMath(text, /\bg\(z\)\s*\*?\s*\(1\s*-\s*g\(z\)\)/g,
    '$g(z)(1-g(z))$')
  text = outsideMath(text, /σ[′']\(z\)\s*=\s*σ\(z\)\s*\(1\s*[−-]\s*σ\(z\)\)/g,
    "$\\sigma'(z)=\\sigma(z)(1-\\sigma(z))$")
  text = outsideMath(text, /\bXX\^T\s*W\s*=\s*λ\s*W\b/g,
    '$XX^{\\mathsf T}W=\\lambda W$')
  text = outsideMath(text, /\bW\^TW\s*=\s*I\b/g,
    '$W^{\\mathsf T}W=I$')
  text = outsideMath(text, /\bXX\^T\b/g, '$XX^{\\mathsf T}$')
  text = outsideMath(text, /\bXXT\b/g, '$XX^{\\mathsf T}$')
  text = outsideMath(text, /\b(z\s*=\s*)?w\^T\s*\*\s*x\b/g,
    (_match: string, prefix: string) => `$${prefix ? 'z=' : ''}w^{\\mathsf T}x$`)
  text = outsideMath(text, /\b([A-Za-z]{1,2})\^T\b/g,
    (_match: string, symbol: string) => `$${symbol}^{\\mathsf T}$`)
  text = outsideMath(text, /\b([A-Za-z])\^\{([^{}]+)\}/g,
    (_match: string, symbol: string, power: string) => `$${symbol}^{${power}}$`)
  text = outsideMath(text, /\b([A-Za-z])_(\d+|[A-Za-z])\b/g,
    (_match: string, symbol: string, subscript: string) => `$${symbol}_{${subscript}}$`)
  text = outsideMath(text, /\b([xw])0\s*=\s*1\b/g,
    (_match: string, symbol: string) => `$${symbol}_0=1$`)
  return text
}
