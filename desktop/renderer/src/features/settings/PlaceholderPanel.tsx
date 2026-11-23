type Props = {
  title: string;
  description: string;
};

export function PlaceholderPanel({ title, description }: Props) {
  return (
    <div className="settings-placeholder">
      <h3>{title}</h3>
      <p>{description}</p>
    </div>
  );
}
