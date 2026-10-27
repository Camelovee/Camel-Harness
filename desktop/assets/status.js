const failed = new URLSearchParams(location.search).get("state") === "failed";
const retry = document.getElementById("retry");
if (failed) {
  document.getElementById("message").textContent = "本地服务未能启动或已意外退出。请检查 ~/.camel/config.json，或重新安装应用。已完成的会话仍保存在本机；重新启动不会自动执行之前的任务。";
  retry.hidden = false;
}
retry.addEventListener("click", async () => {
  retry.disabled = true;
  try { await window.camelDesktop.retryBackend(); }
  catch { retry.disabled = false; }
});
