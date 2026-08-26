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
        a: ({ children, href, title }) => (
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

function remarkStockLinks({
  stockMentions = [],
}: {
  stockMentions?: AgentStockMention[];
}) {
  const mentions = [...stockMentions]
    .filter((item) => item.name && /^\d{6}$/.test(item.symbol))
    .sort((left, right) => right.name.length - left.name.length);

  return (tree: MarkdownNode) => {
    if (mentions.length > 0) {
      linkifyChildren(tree, mentions);
    }
  };
}

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

function stockMentionPath(mention: AgentStockMention) {
  const params = new URLSearchParams({ name: mention.name });
  if (mention.trade_date) {
    params.set("trade_date", mention.trade_date);
  }
  return `/stocks/${encodeURIComponent(mention.symbol)}?${params.toString()}`;
}
