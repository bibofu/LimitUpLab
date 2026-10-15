/** Minimal HAST shape; all attributes and positions remain attached to retained nodes. */
export interface RevealNode {
  type: string;
  tagName?: string;
  value?: string;
  children?: RevealNode[];
  [key: string]: unknown;
}

interface GraphemeSegmenter {
  segment(value: string): Iterable<{ segment: string }>;
}

const Segmenter = (Intl as typeof Intl & {
  Segmenter?: new (locale?: string, options?: { granularity: "grapheme" }) => GraphemeSegmenter;
}).Segmenter;
const segmenter = Segmenter ? new Segmenter(undefined, { granularity: "grapheme" }) : undefined;
const TEXT_UNIT_SIZE = 12;
const PROSE = new Set(["p", "h1", "h2", "h3", "h4", "h5", "h6"]);
const INLINE_CONTAINERS = new Set(["strong", "em", "del", "s", "span", "mark", "sub", "sup", "b", "i", "u"]);

function graphemes(value: string): string[] {
  // Older browsers reveal whole text nodes rather than splitting combined characters.
  return segmenter ? Array.from(segmenter.segment(value), item => item.segment) : [value];
}

function whitespace(node: RevealNode): boolean {
  return node.type === "text" && !(node.value ?? "").trim();
}

function inlineUnits(node: RevealNode): number {
  if (whitespace(node)) return 0;
  if (node.type === "text") return Math.ceil(graphemes(node.value ?? "").length / TEXT_UNIT_SIZE);
  if (INLINE_CONTAINERS.has(node.tagName ?? "")) {
    return (node.children ?? []).reduce((total, child) => total + inlineUnits(child), 0);
  }
  // Links, inline code, images and other inline objects are complete from their first frame.
  return 1;
}

function tableRows(node: RevealNode): number {
  return (node.children ?? []).reduce((total, child) => {
    if (child.tagName === "tr") return total + 1;
    if (child.tagName === "tbody" || child.tagName === "tfoot") return total + tableRows(child);
    return total;
  }, 0);
}

function blockUnits(node: RevealNode): number {
  if (whitespace(node) || node.type === "comment") return 0;
  if (node.type === "root") return (node.children ?? []).reduce((total, child) => total + blockUnits(child), 0);
  if (PROSE.has(node.tagName ?? "")) {
    return Math.max(1, (node.children ?? []).reduce((total, child) => total + inlineUnits(child), 0));
  }
  if (node.tagName === "table") return Math.max(1, tableRows(node));
  if (node.tagName === "ul" || node.tagName === "ol") {
    return Math.max(1, (node.children ?? []).filter(child => child.tagName === "li").length);
  }
  return 1;
}

function revealInline(node: RevealNode, budget: { remaining: number }): boolean {
  const units = inlineUnits(node);
  if (units <= budget.remaining) {
    budget.remaining -= units;
    return true;
  }
  if (budget.remaining <= 0) return false;
  if (node.type === "text") {
    node.value = graphemes(node.value ?? "").slice(0, budget.remaining * TEXT_UNIT_SIZE).join("");
    budget.remaining = 0;
    return true;
  }
  if (INLINE_CONTAINERS.has(node.tagName ?? "")) {
    node.children = (node.children ?? []).filter(child => revealInline(child, budget));
    return true;
  }
  return false;
}

function revealTableRows(node: RevealNode, rows: { remaining: number }): void {
  node.children = (node.children ?? []).filter(child => {
    if (child.tagName === "tr") {
      if (rows.remaining <= 0) return false;
      rows.remaining -= 1;
    } else if (child.tagName === "tbody" || child.tagName === "tfoot") {
      if (rows.remaining <= 0) return false;
      revealTableRows(child, rows);
    }
    return true;
  });
}

function revealBlock(node: RevealNode, budget: { remaining: number }): boolean {
  const units = blockUnits(node);
  if (units <= budget.remaining) {
    budget.remaining -= units;
    return true;
  }
  if (budget.remaining <= 0) return false;
  if (node.type === "root") {
    node.children = (node.children ?? []).filter(child => revealBlock(child, budget));
  } else if (PROSE.has(node.tagName ?? "")) {
    node.children = (node.children ?? []).filter(child => revealInline(child, budget));
  } else if (node.tagName === "table") {
    // The thead is retained together with the first complete body row.
    revealTableRows(node, { remaining: budget.remaining });
    budget.remaining = 0;
  } else if (node.tagName === "ul" || node.tagName === "ol") {
    node.children = (node.children ?? []).filter(child => {
      if (child.tagName !== "li") return true;
      if (budget.remaining <= 0) return false;
      budget.remaining -= 1;
      return true;
    });
  }
  return true;
}

/**
 * Reveal a prefix of an already parsed, verified answer without reparsing partial Markdown.
 * The returned total always describes the complete input tree. Pass a fresh full HAST on each
 * call; Infinity and budgets covering the full answer leave that tree entirely unchanged.
 */
export function revealVerifiedTree(tree: RevealNode, visibleUnits: number): number {
  const total = blockUnits(tree);
  if (visibleUnits >= total) return total;
  const remaining = Number.isFinite(visibleUnits) ? Math.max(0, Math.floor(visibleUnits)) : 0;
  const budget = { remaining };
  if (tree.type === "root") {
    tree.children = (tree.children ?? []).filter(child => revealBlock(child, budget));
  } else if (!revealBlock(tree, budget)) {
    tree.children = [];
    if (tree.type === "text") tree.value = "";
  }
  return total;
}
