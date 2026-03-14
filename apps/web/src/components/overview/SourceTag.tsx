interface SourceTagProps {
  source: string;
}

export function SourceTag({ source }: SourceTagProps) {
  return <span className="source-tag">{source}</span>;
}
