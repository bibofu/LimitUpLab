import ReactMarkdown from "react-markdown";
import { Link } from "react-router-dom";
import remarkGfm from "remark-gfm";

import type { AgentStockMention } from "../types";

interface MarkdownNode {
  type: string;
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

/**
 * Render Agent markdown and link only stock mentions grounded in the response metadata.
 * Existing links remain explicit navigation rather than guessed stock identities.
 */
export function AgentAnswerMarkdown({
  content,
  stockMentions,
}: {
  content: string;
  stockMentions: AgentStockMention[];
}) {
  return (
    <ReactMarkdown
      components={{
        a: /* Render markdown links through the application's internal/external navigation handling. */ ({ children, href, title }) => (
          href?.startsWith("/stocks/") ? (
            <Link title={title} to={href}>{children}</Link>
          ) : (
            <a href={href} rel="noreferrer" target="_blank" title={title}>
              {children}
            </a>
          )
        ),
      }}
      remarkPlugins={[
        remarkGfm,
        [remarkStockLinks, { stockMentions }],
      ]}
    >
      {content}
    </ReactMarkdown>
  );
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
    .filter(/* Keep only entries satisfying this predicate for remarkStockLinks. */ (item) => item.name && /^\d{6}$/.test(item.symbol))
    .sort(/* Compare two entries using the explicit tie-break order for remarkStockLinks. */ (left, right) => right.name.length - left.name.length);

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
