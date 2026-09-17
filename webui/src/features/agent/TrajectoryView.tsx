import type { Activity } from "../../shared/model";

export function TrajectoryView({ activities }: { activities: Activity[] }) {
  return (
    <section className="trajectory-view">
      <div className="trajectory-heading">
        <div>
          <div className="eyebrow">EXECUTION TRAJECTORY</div>
          <h2>运行轨迹</h2>
        </div>
        <span>{activities.length} 个工具步骤</span>
      </div>
      {activities.length ? (
        <div className="trajectory-list">
          {activities.map((activity, index) => (
            <div className="trajectory-row" key={activity.id}>
              <span className="trajectory-line">
                <i>{index + 1}</i>
              </span>
              <div>
                <strong>{activity.name}</strong>
                <small>
                  {activity.status === "running"
                    ? "执行中"
                    : activity.status === "success"
                      ? "已完成"
                      : "失败"}
                </small>
                {activity.result && <pre>{activity.result}</pre>}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="trajectory-empty">
          运行 Agent 后，这里会显示完整的 ReAct 执行轨迹。
        </div>
      )}
    </section>
  );
}
