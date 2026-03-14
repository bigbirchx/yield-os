import { SourceTag } from "@/components/overview/SourceTag";

interface SectionCardProps {
  title: string;
  source?: string;
  children: React.ReactNode;
  empty?: boolean;
  emptyMessage?: string;
}

export function SectionCard({
  title,
  source,
  children,
  empty,
  emptyMessage = "No data",
}: SectionCardProps) {
  return (
    <section className="section-card">
      <div className="section-card-header">
        <span className="section-card-title">{title}</span>
        {source && <SourceTag source={source} />}
      </div>
      <div className="section-card-body">
        {empty ? (
          <div className="section-empty">{emptyMessage}</div>
        ) : (
          children
        )}
      </div>
    </section>
  );
}
