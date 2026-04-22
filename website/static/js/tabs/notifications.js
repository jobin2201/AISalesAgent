window.NotificationsTab = (() => {
  function init() {
    const unread = document.getElementById("btnNotifUnread");
    const all = document.getElementById("btnNotifAll");
    const readAll = document.getElementById("btnNotifReadAll");

    if (unread) {
      unread.addEventListener("click", async () => {
        window.AppState.notificationMode = "unread";
        await window.AppActions.refreshNotifications();
      });
    }

    if (all) {
      all.addEventListener("click", async () => {
        window.AppState.notificationMode = "all";
        await window.AppActions.refreshNotifications();
      });
    }

    if (readAll) {
      readAll.addEventListener("click", async () => {
        await window.AppActions.api("/api/notifications/read-all", { method: "POST" });
        await window.AppActions.refreshNotifications();
      });
    }
  }

  return { init };
})();
