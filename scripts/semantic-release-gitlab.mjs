// @semantic-release/gitlab, except that its `success` step cannot fail the
// release job.
//
// `success` runs after everything a release is: the tag, the GitLab release,
// the release commit and the GitHub release HACS installs from. All it adds is
// a "This MR is included in version X" note on each merged MR, one POST per
// MR, and neither got nor the plugin retries a POST. A dropped connection on
// any of them turned a finished release into a red job, which reads as "the
// release failed" and invites re-running a job that has nothing left to do.
// A note that didn't post is logged as a warning instead.
import * as gitlab from "@semantic-release/gitlab";

export const { verifyConditions, publish, fail } = gitlab;

export function tolerant(step) {
  return async (pluginConfig, context) => {
    try {
      return await step(pluginConfig, context);
    } catch (error) {
      context.logger.warn(
        "The release is published, but commenting on its merge requests " +
          `failed: ${error.message ?? error}`,
      );
      return undefined;
    }
  };
}

export const success = tolerant(gitlab.success);
