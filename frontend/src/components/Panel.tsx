import type { ReactNode } from "react";

interface PanelProps {
  title: string;
  icon: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}

export function Panel({ title, icon, actions, children }: PanelProps) {
  return (
    <section className="panel">
      <header>
        <div>
          {icon}
          <h2>{title}</h2>
        </div>
        {actions}
      </header>
      {children}
    </section>
  );
}
