import type { AdminQualityClient, EvaluationHistory, QualityRunJob, QualityRunJobs } from "./adminQuality";

export type JudgeSnapshot = { jobs: QualityRunJobs; evaluations: EvaluationHistory };
export type JudgeWatchResult = "done" | "failed" | "deferred" | "waiting" | "idle";

export function qualityJobAvailableAt(value: number | string): number {
  return typeof value === "number" ? value * 1000 : Date.parse(value);
}

export function judgeJobState(job?: QualityRunJob, now = Date.now()): JudgeWatchResult | "active" {
  if (!job) return "idle";
  if (job.status === "done") return "done";
  if (job.status === "dead" || job.status === "superseded") return "failed";
  if (job.status === "pending" && qualityJobAvailableAt(job.available_at) > now + 30_000) return "deferred";
  return "active";
}

export async function readJudgeSnapshot(api: AdminQualityClient, runId: string, signal: AbortSignal): Promise<JudgeSnapshot> {
  const [jobs, evaluations] = await Promise.all([api.runJobs(runId, signal), api.evaluations(runId, signal)]);
  return { jobs, evaluations };
}

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(new DOMException("Aborted", "AbortError")); return; }
    const abort = () => { clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
}

/** Observation only: refreshing never creates or retries a model task. */
export async function watchJudge(
  api: AdminQualityClient, runId: string, jobId: string, signal: AbortSignal,
  onSnapshot: (snapshot: JudgeSnapshot) => void,
  options: { maxPolls?: number; wait?: (ms: number, signal: AbortSignal) => Promise<void> } = {},
): Promise<JudgeWatchResult> {
  for (let poll = 0; poll < (options.maxPolls ?? 20); poll++) {
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    const snapshot = await readJudgeSnapshot(api, runId, signal);
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    onSnapshot(snapshot);
    const state = judgeJobState(snapshot.jobs.items.find(job => job.id === jobId));
    if (state !== "active") return state;
    if (poll + 1 < (options.maxPolls ?? 20)) await (options.wait ?? delay)(3000, signal);
  }
  return "waiting";
}
