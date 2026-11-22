type Props = {
  title: string;
  description: string;
};

export function PlaceholderPanel({ title, description }: Props) {
  return (
    <div className="settings-placeholder">
      <h3>{title}</h3>
      <p>{description}</p>
      <small>请通过配置文件管理。</small>
    </div>
  );
}
