type Props = {
  title: string;
  description: string;
};

export function PlaceholderPanel({ title, description }: Props) {
  return (
    <div className="settings-placeholder">
      <h3>{title}</h3>
      <p>{description}</p>
      <small>此面板为预留入口，配置能力将在后续版本提供。</small>
    </div>
  );
}
