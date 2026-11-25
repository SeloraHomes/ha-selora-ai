// The fix_camera home suggestion: a camera the last Health audit found
// costing the hub, or sending no picture (frigate/repair.py). Its row says
// why from the audit's figures; acting on it opens the card the server
// writes (selora_ai/frigate/fix_start), which names the change.

// The Health audit's figures for a fix_camera row, as words can show them.
export function cameraReportValues(report) {
  const size = (pair) => (pair ? `${pair[0]}×${pair[1]}` : "");
  return {
    stream: size(report?.stream),
    analysed: size(report?.analysed),
    fps:
      typeof report?.skipped_fps === "number"
        ? Math.round(report.skipped_fps * 10) / 10
        : "",
  };
}

// A camera the last Health audit found costing the hub, or sending no
// picture. The title names its most telling issue (the server lists them in
// that order); the card the server opens names the change and what only the
// user can do. The cameras dropping frames because of it are named too.
export function describeCameraFix(s, t, tl) {
  const report = s.camera_report || {};
  const issues = new Set(report.issues || []);
  const lead = (report.issues || [])[0];
  const why = [];
  if (issues.has("detect_stream_unreadable") || issues.has("no_video")) {
    why.push(
      t(
        "home_suggestion_camera_no_picture_why",
        "Selora Vision gets no picture from it. Check it is powered, on the network, and its stream is on.",
      ),
    );
  }
  if (issues.has("skipping_frames")) {
    why.push(
      report.skipped_fps != null
        ? t(
            "home_suggestion_camera_skipping_why",
            "It drops {fps} frames a second because the hub cannot keep up, so detections arrive late or are missed.",
          )
        : t(
            "home_suggestion_camera_skipping_why_plain",
            "It drops frames because the hub cannot keep up, so detections arrive late or are missed.",
          ),
    );
  }
  if (issues.has("detect_above_720p")) {
    why.push(
      t(
        "home_suggestion_camera_720p_why",
        "Selora Vision analyses it at {analysed}. 720p is enough to spot people and faces, for a fraction of the work.",
      ),
    );
  }
  if (issues.has("detect_stream_heavy")) {
    why.push(
      report.records_too
        ? t(
            "home_suggestion_camera_main_stream_why",
            "Selora Vision analyses it from its {stream} main stream, so the hub decodes every frame in full. Its substream gives the same detections for a fraction of the work.",
          )
        : t(
            "home_suggestion_camera_heavy_sub_why",
            "Its substream is {stream}, so the hub decodes every frame in full. Lowered to 720p in the camera's own settings, it gives the same detections for a fraction of the work.",
          ),
    );
  }
  if (issues.has("high_cpu")) {
    why.push(
      t(
        "home_suggestion_camera_busy_why",
        "Constant motion in view (trees, a road) keeps the hub busy analysing it. A motion mask over those areas brings it down.",
      ),
    );
  }
  if (s.members?.length) {
    why.push(
      tl(
        "home_suggestion_camera_slows_why",
        "Until it is fixed, the hub also falls behind on {members}.",
      ),
    );
  }
  let title;
  if (lead === "detect_stream_unreadable" || lead === "no_video") {
    title = t(
      "home_suggestion_camera_no_picture",
      "{entity} sends Selora Vision no picture",
    );
  } else if (lead === "skipping_frames") {
    title = t(
      "home_suggestion_camera_behind",
      "{entity} is falling behind in Selora Vision",
    );
  } else if (lead === "high_cpu") {
    title = t("home_suggestion_camera_busy", "{entity} keeps your hub busy");
  } else {
    title = t(
      "home_suggestion_camera_fix",
      "Fix how Selora Vision watches {entity}",
    );
  }
  return {
    icon: "mdi:cctv",
    title,
    prompt: t(
      "home_suggestion_camera_prompt",
      "Fix how Selora Vision analyses the camera {entity} ({ref}).",
    ),
    why,
    act: t("home_suggestion_camera_act", "Fix it"),
    ask: t(
      "home_suggestion_camera_ask",
      "Fix how Selora Vision watches {entity}",
    ),
  };
}
