import { memo, useEffect, useRef, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import { Link } from "react-router-dom";
import remarkGfm from "remark-gfm";

import type { AgentStockMention } from "../types";
import { revealVerifiedTree, type RevealNode } from "../utils/verifiedAnswerReveal";

interface MarkdownNode {
  type: string;
  tagName?: string;
  value?: string;
  url?: string;
  children?: MarkdownNode[];
}

const SKIPPED_NODE_TYPES = new Set([
  "code",
  "definition",
  "html",
  "inlineCode",
  "link",
  "linkReference",
]);

const MARKDOWN_COMPONENTS: Components = {
  a: ({ children, href, title }) => (
    href?.startsWith("/stocks/") ? (
      <Link title={title} to={href}>{children}</Link>
    ) : (
      <a href={href} rel="noreferrer" target="_blank" title={title}>{children}</a>
    )
  ),
};

/**
 * Render Agent markdown and link only stock mentions grounded in the response metadata.
 * Existing links remain explicit navigation rather than guessed stock identities.
 */
export const AgentAnswerMarkdown = memo(function AgentAnswerMarkdown({
  content,
  stockMentions,
  animate = false,
  suppressTables = false,
  duplicateListLabels = [],
  onRevealProgress,
  onRevealComplete,
}: {
  content: string;
  stockMentions: AgentStockMention[];
  animate?: boolean;
  suppressTables?: boolean;
  duplicateListLabels?: string[];
  onRevealProgress?: () => void;
  onRevealComplete?: () => void;
}) {
  const [revealed, setRevealed] = useState({ content, units: 1 });
  const plan = useRef({ total: 0 });
  const revealProgress = useRef(onRevealProgress);
  revealProgress.current = onRevealProgress;
  const revealComplete = useRef(onRevealComplete);
  revealComplete.current = onRevealComplete;
  const finishReveal = useRef<() => void>(() => {});
  const reducedMotion = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const canAnimate = animate && content.length <= 20000 && content.split("\n").length <= 400;
  const visibleUnits = !canAnimate || reducedMotion ? Infinity : revealed.content === content ? revealed.units : 1;
  const revealing = Number.isFinite(visibleUnits);

  useEffect(() => {
    const media = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!canAnimate || media?.matches || plan.current.total <= 1) {
      setRevealed({ content, units: Infinity });
      return;
    }
    const total = plan.current.total;
    const duration = Math.min(2400, Math.max(210, (total - 1) * 70));
    const started = performance.now();
    let lastUpdate = started;
    let frame = 0;
    let active = true;
    setRevealed({ content, units: 1 });
    const finish = () => {
      active = false;
      window.cancelAnimationFrame(frame);
      setRevealed({ content, units: Infinity });
    };
    finishReveal.current = finish;
    const tick = (now: number) => {
      if (!active) return;
      if (now - started >= duration) {
        finish();
        return;
      }
      if (now - lastUpdate >= 70) {
        lastUpdate = now;
        setRevealed({ content, units: 1 + Math.floor((total - 1) * (now - started) / duration) });
      }
      frame = window.requestAnimationFrame(tick);
    };
    const onMotionChange = () => { if (media?.matches) finish(); };
    media?.addEventListener("change", onMotionChange);
    frame = window.requestAnimationFrame(tick);
    return () => {
      active = false;
      window.cancelAnimationFrame(frame);
      media?.removeEventListener("change", onMotionChange);
      finishReveal.current = () => {};
    };
  }, [canAnimate, content]);

  useEffect(() => { revealProgress.current?.(); }, [visibleUnits]);
  useEffect(() => { if (!revealing) revealComplete.current?.(); }, [revealing, content]);

  return (
    <>
    <ReactMarkdown
      components={MARKDOWN_COMPONENTS}
      remarkPlugins={[
        remarkGfm,
        [remarkStockLinks, { stockMentions }],
      ]}
      rehypePlugins={[
        ...(suppressTables ? [rehypeSuppressTables] : []),
        ...(duplicateListLabels.length
          ? [[rehypeSuppressDuplicateLists, { labels: duplicateListLabels }] as any]
          : []),
        [rehypeVerifiedReveal, { visibleUnits, plan }],
      ]}
    >
      {content}
    </ReactMarkdown>
    {revealing ? (
      <button className="chat-reveal-all" type="button" onClick={() => finishReveal.current()}>
        立即显示全部
      </button>
    ) : null}
    </>
  );
});

/** Remove model-written tables when the server has an authoritative display block. */
function rehypeSuppressTables() {
  return (tree: MarkdownNode) => {
    stripTableNodes(tree);
  };
}

function stripTableNodes(parent: MarkdownNode) {
  if (!parent.children) return;
  parent.children = parent.children.filter(child => (
    child.type !== "table" && !(child.type === "element" && child.tagName === "table")
  ));
  parent.children.forEach(stripTableNodes);
}

/** Remove a list only when every item is a label already rendered by a display block. */
function rehypeSuppressDuplicateLists({ labels }: { labels: string[] }) {
  const normalized = new Set(labels.map(normalizeListLabel).filter(Boolean));
  return (tree: MarkdownNode) => {
    stripDuplicateLists(tree, normalized);
  };
}

function normalizeListLabel(value: string) {
  return value
    .replace(/\s+/g, "")
    .replace(/[（）()：:·,，、\-—_]/g, "")
    .toLocaleLowerCase();
}

function nodeText(node: MarkdownNode): string {
  if (node.value) return node.value;
  return (node.children ?? []).map(nodeText).join("");
}

function stripDuplicateLists(parent: MarkdownNode, labels: Set<string>) {
  if (!parent.children) return;
  parent.children = parent.children.filter(child => {
    if (child.type === "element" && (child.tagName === "ul" || child.tagName === "ol")) {
      const items = (child.children ?? []).filter(item => item.type === "element" && item.tagName === "li");
      if (items.length >= 2 && items.every(item => labels.has(normalizeListLabel(nodeText(item))))) {
        return false;
      }
    }
    return true;
  });
  parent.children.forEach(child => stripDuplicateLists(child, labels));
}

/** Parse the complete approved Markdown first, then reveal stable rendered nodes. */
function rehypeVerifiedReveal({ visibleUnits, plan }: { visibleUnits: number; plan: { current: { total: number } } }) {
  return (tree: RevealNode) => { plan.current.total = revealVerifiedTree(tree, visibleUnits); };
}

/**
 * Build the markdown-tree transformer that adds links for known stock mentions.
 */
function remarkStockLinks({
  stockMentions = [],
}: {
  stockMentions?: AgentStockMention[];
}) {
  const mentions = [...stockMentions]
    .filter((item) => item.name && /^\d{6}$/.test(item.symbol))
    .sort((left, right) => right.name.length - left.name.length);

  return /* Transform the markdown tree when the remark pipeline invokes this plugin. */ (tree: MarkdownNode) => {
    if (mentions.length > 0) {
      linkifyChildren(tree, mentions);
    }
  };
}

/**
 * Walk markdown child nodes and replace eligible text with grounded stock links.
 */
function linkifyChildren(parent: MarkdownNode, mentions: AgentStockMention[]) {
  if (!parent.children || SKIPPED_NODE_TYPES.has(parent.type)) {
    return;
  }

  const children: MarkdownNode[] = [];
  for (const child of parent.children) {
    if (child.type === "text" && child.value) {
      children.push(...linkifyText(child.value, mentions));
    } else {
      linkifyChildren(child, mentions);
      children.push(child);
    }
  }
  parent.children = children;
}

/**
 * Split a text node around known stock names/codes, retaining untouched text between matches.
 */
function linkifyText(value: string, mentions: AgentStockMention[]): MarkdownNode[] {
  const nodes: MarkdownNode[] = [];
  let cursor = 0;

  while (cursor < value.length) {
    let matchedMention: AgentStockMention | null = null;
    let matchedIndex = -1;
    for (const mention of mentions) {
      const index = value.indexOf(mention.name, cursor);
      if (
        index >= 0
        && (matchedIndex < 0 || index < matchedIndex)
      ) {
        matchedMention = mention;
        matchedIndex = index;
      }
    }

    if (!matchedMention || matchedIndex < 0) {
      nodes.push({ type: "text", value: value.slice(cursor) });
      break;
    }
    if (matchedIndex > cursor) {
      nodes.push({ type: "text", value: value.slice(cursor, matchedIndex) });
    }
    nodes.push({
      type: "link",
      url: stockMentionPath(matchedMention),
      children: [{ type: "text", value: matchedMention.name }],
    });
    cursor = matchedIndex + matchedMention.name.length;
  }

  return nodes;
}

/**
 * Build the stock-detail link from a grounded mention's symbol, name and optional date.
 */
function stockMentionPath(mention: AgentStockMention) {
  const params = new URLSearchParams({ name: mention.name });
  if (mention.trade_date) {
    params.set("trade_date", mention.trade_date);
  }
  return `/stocks/${encodeURIComponent(mention.symbol)}?${params.toString()}`;
}
