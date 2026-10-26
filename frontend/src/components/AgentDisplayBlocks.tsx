import type { AgentDisplayBlock } from "../types";

function cellText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.map(item => String(item)).join("、");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/**
 * Render server-projected tables without asking the model to reproduce columns.
 * The source tool keeps the complete rows; this component shows only the
 * columns selected by memory and the current request.
 */
export function AgentDisplayBlocks({ blocks }: { blocks?: AgentDisplayBlock[] }) {
  if (!blocks?.length) return null;
  return (
    <div className="agent-display-blocks">
      {blocks.map(block => (
        <section
          className="agent-display-block"
          key={`${block.source_tool}-${block.source_tool_index}`}
          aria-label={`${block.source_tool} 结构化展示`}
        >
          <div className="agent-display-caption">
            结构化展示 · 共 {block.row_count} 条
            {block.truncated ? "（来源截断）" : ""}
          </div>
          <div className="agent-display-table-wrap">
            <table>
              <thead>
                <tr>
                  {block.columns.map(column => (
                    <th key={column} scope="col">
                      {block.column_labels[column] || column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.rows.length ? block.rows.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {block.columns.map(column => (
                      <td key={column}>{cellText(row[column])}</td>
                    ))}
                  </tr>
                )) : (
                  <tr>
                    <td colSpan={Math.max(block.columns.length, 1)}>无数据</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {block.missing_columns.length ? (
            <small className="agent-display-note">
              缺少字段：{block.missing_columns.join("、")}
            </small>
          ) : null}
        </section>
      ))}
    </div>
  );
}
