import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  disableAdminUser,
  enableAdminUser,
  getAdminDashboard,
  listAdminJobs,
  listAdminPlans,
  listAdminUsers,
  updateAdminPlan,
} from "../api/adminClient";
import { ApiError } from "../api/http";
import { useDialogA11y } from "../hooks/useDialogA11y";
import type { AdminDashboard, AdminJobSummary, AdminPlan, AdminUserSummary } from "../types/admin";

// Applies to both the users and jobs tabs -- neither exposes a page-size
// control (not asked for), only Previous/Next over a fixed page size.
const PAGE_SIZE = 50;

interface AdminScreenProps {
  authToken: string;
  onClose: () => void;
}

type Tab = "dashboard" | "users" | "jobs" | "plans";
const TABS: Tab[] = ["dashboard", "users", "jobs", "plans"];

function genericErrorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : "Something went wrong. Please try again.";
}

/** Every tab's own data fetch can independently fail with a 401/403 if an
 * admin's session was revoked (or downgraded) while this screen was open
 * -- ApiError's own message (the backend's real "Please sign in..."/
 * "Admin access required." text) already explains that clearly, so
 * nothing admin-specific is invented here. Backend authorization is what
 * actually enforces this; this screen only has to render whatever it says. */

export default function AdminScreen({ authToken, onClose }: AdminScreenProps) {
  const [tab, setTab] = useState<Tab>("dashboard");

  return (
    <div className="mx-auto flex min-h-screen max-w-4xl flex-col gap-6 px-4 pb-10 pt-8">
      <header className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-slate-900">Admin</h1>
        <button
          type="button"
          onClick={onClose}
          className="min-h-11 rounded-full border border-slate-200 px-4 text-sm font-semibold text-slate-600"
        >
          Back to app
        </button>
      </header>

      <nav className="flex flex-wrap gap-2" aria-label="Admin sections">
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            aria-pressed={tab === t}
            onClick={() => setTab(t)}
            className={`min-h-11 rounded-full px-4 text-sm font-semibold capitalize transition ${
              tab === t ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600"
            }`}
          >
            {t}
          </button>
        ))}
      </nav>

      {tab === "dashboard" && <DashboardTab authToken={authToken} />}
      {tab === "users" && <UsersTab authToken={authToken} />}
      {tab === "jobs" && <JobsTab authToken={authToken} />}
      {tab === "plans" && <PlansTab authToken={authToken} />}
    </div>
  );
}

/** Shared Previous/Next pager for the users and jobs tabs -- both fetch a
 * fixed-size page via limit/offset (see adminClient.ts) and only need to
 * move one page at a time, not jump to an arbitrary page number. */
function PaginationControls({
  offset,
  count,
  total,
  onPrev,
  onNext,
}: {
  offset: number;
  count: number;
  total: number;
  onPrev: () => void;
  onNext: () => void;
}) {
  if (total === 0) return null;
  const rangeStart = count === 0 ? 0 : offset + 1;
  const rangeEnd = offset + count;
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <button
        type="button"
        onClick={onPrev}
        disabled={offset === 0}
        aria-label="Previous page"
        className="min-h-11 rounded-full border border-slate-200 px-4 text-sm font-semibold text-slate-600 disabled:opacity-50"
      >
        Previous
      </button>
      <p className="text-xs text-slate-400" aria-live="polite">
        Showing {rangeStart}–{rangeEnd} of {total}
      </p>
      <button
        type="button"
        onClick={onNext}
        disabled={rangeEnd >= total}
        aria-label="Next page"
        className="min-h-11 rounded-full border border-slate-200 px-4 text-sm font-semibold text-slate-600 disabled:opacity-50"
      >
        Next
      </button>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-2xl bg-white p-4 shadow-sm">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-bold text-slate-900">{value}</p>
    </div>
  );
}

type LoadState<T> = { status: "loading" } | { status: "error"; message: string } | ({ status: "ready" } & T);

function DashboardTab({ authToken }: { authToken: string }) {
  // useState's own initial value already covers "loading" -- authToken is
  // effectively constant for this component's lifetime (set once when
  // AdminScreen mounts for a given admin session), so this effect only
  // ever runs once and never needs to reset back to loading mid-lifetime.
  const [state, setState] = useState<LoadState<{ data: AdminDashboard }>>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    getAdminDashboard(authToken)
      .then((data) => {
        if (!cancelled) setState({ status: "ready", data });
      })
      .catch((err) => {
        if (!cancelled) setState({ status: "error", message: genericErrorMessage(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [authToken]);

  if (state.status === "loading") {
    return (
      <p role="status" className="text-sm text-slate-500">
        Loading dashboard…
      </p>
    );
  }
  if (state.status === "error") {
    return (
      <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        {state.message}
      </p>
    );
  }

  const { data } = state;
  const statusEntries = Object.entries(data.jobs_by_status);

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Total users" value={data.total_users} />
        <StatCard label="Active users" value={data.active_users} />
        <StatCard label="Active jobs" value={`${data.active_job_count} / ${data.max_active_job_capacity}`} />
        <StatCard
          label="Avg. duration"
          value={data.avg_processing_duration_seconds != null ? `${Math.round(data.avg_processing_duration_seconds)}s` : "—"}
        />
      </div>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-700">Jobs by status</h2>
        {statusEntries.length === 0 ? (
          <p className="text-sm text-slate-500">No jobs yet.</p>
        ) : (
          <ul className="flex flex-wrap gap-2">
            {statusEntries.map(([status, count]) => (
              <li key={status} className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">
                {status}: {count}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-700">Recent failed jobs</h2>
        {data.recent_failed_jobs.length === 0 ? (
          <p className="text-sm text-slate-500">No recent failures.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {data.recent_failed_jobs.map((job) => (
              <li key={job.job_id} className="rounded-xl border border-slate-200 p-3 text-sm">
                <p className="font-mono text-xs text-slate-500">{job.job_id}</p>
                <p className="text-red-700">{job.error ?? "No error message recorded."}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function UsersTab({ authToken }: { authToken: string }) {
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [state, setState] = useState<LoadState<{ users: AdminUserSummary[]; total: number }>>({ status: "loading" });
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingUserId, setPendingUserId] = useState<number | null>(null);

  const load = useCallback(
    (searchTerm: string, pageOffset: number) => {
      setState({ status: "loading" });
      listAdminUsers(authToken, { search: searchTerm || undefined, limit: PAGE_SIZE, offset: pageOffset })
        .then((res) => {
          setOffset(pageOffset);
          setState({ status: "ready", users: res.users, total: res.total });
        })
        .catch((err) => setState({ status: "error", message: genericErrorMessage(err) }));
    },
    [authToken],
  );

  useEffect(() => {
    load("", 0);
  }, [load]);

  const handleSearchSubmit = (e: FormEvent) => {
    e.preventDefault();
    load(search, 0); // a new search always starts back at page 1
  };

  const handlePrevPage = () => load(search, Math.max(0, offset - PAGE_SIZE));
  const handleNextPage = () => load(search, offset + PAGE_SIZE);

  const handleToggleActive = async (user: AdminUserSummary) => {
    setActionError(null);
    setPendingUserId(user.id);
    try {
      const updated = user.is_active
        ? await disableAdminUser(user.id, authToken)
        : await enableAdminUser(user.id, authToken);
      setState((prev) =>
        prev.status === "ready" ? { ...prev, users: prev.users.map((u) => (u.id === updated.id ? updated : u)) } : prev,
      );
    } catch (err) {
      setActionError(genericErrorMessage(err));
    } finally {
      setPendingUserId(null);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <form onSubmit={handleSearchSubmit} className="flex gap-2">
        <label htmlFor="admin-user-search" className="sr-only">
          Search users by email
        </label>
        <input
          id="admin-user-search"
          type="search"
          placeholder="Search by email"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="min-h-11 flex-1 rounded-lg border border-slate-200 px-3 py-2.5 text-sm"
        />
        <button type="submit" className="min-h-11 rounded-lg bg-indigo-600 px-4 text-sm font-semibold text-white">
          Search
        </button>
      </form>

      {actionError && (
        <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {actionError}
        </p>
      )}

      {state.status === "loading" && (
        <p role="status" className="text-sm text-slate-500">
          Loading users…
        </p>
      )}
      {state.status === "error" && (
        <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {state.message}
        </p>
      )}
      {state.status === "ready" && state.users.length === 0 && <p className="text-sm text-slate-500">No users found.</p>}
      {state.status === "ready" && state.users.length > 0 && (
        <>
          <div className="overflow-x-auto">
            <table aria-label="Users" className="w-full min-w-[640px] text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-xs font-semibold uppercase text-slate-500">
                  <th scope="col" className="py-2 pr-3">
                    Email
                  </th>
                  <th scope="col" className="py-2 pr-3">
                    Plan
                  </th>
                  <th scope="col" className="py-2 pr-3">
                    Created
                  </th>
                  <th scope="col" className="py-2 pr-3">
                    Admin
                  </th>
                  <th scope="col" className="py-2 pr-3">
                    Status
                  </th>
                  <th scope="col" className="py-2">
                    Action
                  </th>
                </tr>
              </thead>
              <tbody>
                {state.users.map((user) => (
                  <tr key={user.id} className="border-b border-slate-100">
                    <td className="py-2 pr-3">{user.email}</td>
                    <td className="py-2 pr-3">{user.plan}</td>
                    <td className="py-2 pr-3">{new Date(user.created_at).toLocaleDateString()}</td>
                    <td className="py-2 pr-3">{user.is_admin ? "Yes" : "No"}</td>
                    <td className="py-2 pr-3">{user.is_active ? "Active" : "Disabled"}</td>
                    <td className="py-2">
                      <button
                        type="button"
                        disabled={pendingUserId === user.id}
                        onClick={() => void handleToggleActive(user)}
                        className="min-h-11 rounded-full border border-slate-200 px-3 text-xs font-semibold text-slate-600 disabled:opacity-50"
                      >
                        {pendingUserId === user.id ? "Working…" : user.is_active ? "Disable" : "Enable"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <PaginationControls
            offset={offset}
            count={state.users.length}
            total={state.total}
            onPrev={handlePrevPage}
            onNext={handleNextPage}
          />
        </>
      )}
    </div>
  );
}

function statusBadgeClass(status: string): string {
  switch (status) {
    case "completed":
      return "bg-green-100 text-green-700";
    case "failed":
      return "bg-red-100 text-red-700";
    case "processing":
      return "bg-amber-100 text-amber-700";
    case "cancelled":
      return "bg-slate-200 text-slate-600";
    default:
      return "bg-slate-100 text-slate-600";
  }
}

const JOB_STATUS_OPTIONS = ["pending", "processing", "completed", "failed", "cancelled"];

function JobsTab({ authToken }: { authToken: string }) {
  const [statusFilter, setStatusFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [state, setState] = useState<LoadState<{ jobs: AdminJobSummary[]; total: number }>>({ status: "loading" });

  const load = useCallback(
    (status: string, pageOffset: number) => {
      setState({ status: "loading" });
      listAdminJobs(authToken, { status: status || undefined, limit: PAGE_SIZE, offset: pageOffset })
        .then((res) => {
          setOffset(pageOffset);
          setState({ status: "ready", jobs: res.jobs, total: res.total });
        })
        .catch((err) => setState({ status: "error", message: genericErrorMessage(err) }));
    },
    [authToken],
  );

  useEffect(() => {
    load("", 0);
  }, [load]);

  const handleFilterChange = (value: string) => {
    setStatusFilter(value);
    load(value, 0); // changing the filter always starts back at page 1
  };

  const handlePrevPage = () => load(statusFilter, Math.max(0, offset - PAGE_SIZE));
  const handleNextPage = () => load(statusFilter, offset + PAGE_SIZE);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <label htmlFor="admin-job-status" className="text-sm font-medium text-slate-700">
          Status
        </label>
        <select
          id="admin-job-status"
          value={statusFilter}
          onChange={(e) => handleFilterChange(e.target.value)}
          className="min-h-11 rounded-lg border border-slate-200 px-3 text-sm"
        >
          <option value="">All</option>
          {JOB_STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {s.charAt(0).toUpperCase() + s.slice(1)}
            </option>
          ))}
        </select>
      </div>

      {state.status === "loading" && (
        <p role="status" className="text-sm text-slate-500">
          Loading jobs…
        </p>
      )}
      {state.status === "error" && (
        <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {state.message}
        </p>
      )}
      {state.status === "ready" && state.jobs.length === 0 && <p className="text-sm text-slate-500">No jobs found.</p>}
      {state.status === "ready" && state.jobs.length > 0 && (
        <>
          <div className="overflow-x-auto">
            <table aria-label="Jobs" className="w-full min-w-[720px] text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-xs font-semibold uppercase text-slate-500">
                  <th scope="col" className="py-2 pr-3">
                    Job ID
                  </th>
                  <th scope="col" className="py-2 pr-3">
                    User
                  </th>
                  <th scope="col" className="py-2 pr-3">
                    Status
                  </th>
                  <th scope="col" className="py-2 pr-3">
                    Category
                  </th>
                  <th scope="col" className="py-2 pr-3">
                    Type
                  </th>
                  <th scope="col" className="py-2 pr-3">
                    Created
                  </th>
                  <th scope="col" className="py-2">
                    Duration
                  </th>
                </tr>
              </thead>
              <tbody>
                {state.jobs.map((job) => (
                  <tr key={job.job_id} className="border-b border-slate-100 align-top">
                    <td className="py-2 pr-3 font-mono text-xs" title={job.job_id}>
                      {job.job_id.slice(0, 8)}…
                    </td>
                    <td className="py-2 pr-3">{job.user_id ?? "Anonymous"}</td>
                    <td className="py-2 pr-3">
                      <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${statusBadgeClass(job.status)}`}>
                        {job.status}
                      </span>
                      {job.status === "failed" && job.error && (
                        <p className="mt-1 max-w-[220px] text-xs text-red-600">{job.error}</p>
                      )}
                    </td>
                    <td className="py-2 pr-3">{job.category}</td>
                    <td className="py-2 pr-3">{job.garment_photo_type}</td>
                    <td className="py-2 pr-3">{new Date(job.created_at).toLocaleString()}</td>
                    <td className="py-2">
                      {job.processing_duration_seconds != null ? `${Math.round(job.processing_duration_seconds)}s` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <PaginationControls
            offset={offset}
            count={state.jobs.length}
            total={state.total}
            onPrev={handlePrevPage}
            onNext={handleNextPage}
          />
        </>
      )}
    </div>
  );
}

function PlansTab({ authToken }: { authToken: string }) {
  const [state, setState] = useState<LoadState<{ plans: AdminPlan[] }>>({ status: "loading" });
  const [editingPlan, setEditingPlan] = useState<AdminPlan | null>(null);

  useEffect(() => {
    let cancelled = false;
    listAdminPlans(authToken)
      .then((plans) => {
        if (!cancelled) setState({ status: "ready", plans });
      })
      .catch((err) => {
        if (!cancelled) setState({ status: "error", message: genericErrorMessage(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [authToken]);

  const handlePlanSaved = (updated: AdminPlan) => {
    setState((prev) =>
      prev.status === "ready" ? { ...prev, plans: prev.plans.map((p) => (p.name === updated.name ? updated : p)) } : prev,
    );
    setEditingPlan(null);
  };

  if (state.status === "loading") {
    return (
      <p role="status" className="text-sm text-slate-500">
        Loading plans…
      </p>
    );
  }
  if (state.status === "error") {
    return (
      <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        {state.message}
      </p>
    );
  }
  if (state.plans.length === 0) {
    return <p className="text-sm text-slate-500">No plans configured.</p>;
  }

  return (
    <>
      <ul className="flex flex-col gap-3">
        {state.plans.map((plan) => (
          <li key={plan.name} className="rounded-2xl bg-white p-4 shadow-sm">
            <div className="flex items-center justify-between gap-2">
              <p className="font-semibold capitalize text-slate-900">{plan.name}</p>
              <button
                type="button"
                onClick={() => setEditingPlan(plan)}
                className="min-h-11 rounded-full border border-slate-200 px-3 text-xs font-semibold text-slate-600"
              >
                Edit
              </button>
            </div>
            <dl className="mt-2 grid grid-cols-3 gap-2 text-xs text-slate-500">
              <div>
                <dt className="font-medium text-slate-400">Per day</dt>
                <dd>{plan.max_generations_per_day ?? "Unlimited"}</dd>
              </div>
              <div>
                <dt className="font-medium text-slate-400">Per month</dt>
                <dd>{plan.max_generations_per_month ?? "Unlimited"}</dd>
              </div>
              <div>
                <dt className="font-medium text-slate-400">Max steps</dt>
                <dd>{plan.max_num_timesteps ?? "Default"}</dd>
              </div>
            </dl>
          </li>
        ))}
      </ul>
      {editingPlan && (
        <PlanEditModal plan={editingPlan} authToken={authToken} onClose={() => setEditingPlan(null)} onSave={handlePlanSaved} />
      )}
    </>
  );
}

/** Edits an *existing* plan's three limit fields only -- never creates or
 * deletes a plan (see backend api/admin.py's update_plan docstring). An
 * empty field means "unlimited" (null), matching Plan's own
 * nullable-means-unlimited columns; the input is otherwise a plain number
 * field, validated server-side regardless of what's submitted here. */
function PlanEditModal({
  plan,
  authToken,
  onClose,
  onSave,
}: {
  plan: AdminPlan;
  authToken: string;
  onClose: () => void;
  onSave: (updated: AdminPlan) => void;
}) {
  const [maxPerDay, setMaxPerDay] = useState(plan.max_generations_per_day?.toString() ?? "");
  const [maxPerMonth, setMaxPerMonth] = useState(plan.max_generations_per_month?.toString() ?? "");
  const [maxTimesteps, setMaxTimesteps] = useState(plan.max_num_timesteps?.toString() ?? "");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const panelRef = useDialogA11y(onClose);

  const parseField = (value: string): number | null => (value.trim() === "" ? null : Number(value));

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      const updated = await updateAdminPlan(
        plan.name,
        {
          max_generations_per_day: parseField(maxPerDay),
          max_generations_per_month: parseField(maxPerMonth),
          max_num_timesteps: parseField(maxTimesteps),
        },
        authToken,
      );
      onSave(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center" onClick={onClose}>
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="plan-edit-modal-title"
        className="max-h-[85vh] w-full max-w-sm overflow-y-auto rounded-t-2xl bg-white p-6 pb-8 shadow-xl sm:rounded-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 id="plan-edit-modal-title" className="text-lg font-bold capitalize text-slate-900">
            Edit {plan.name} plan
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex h-11 w-11 items-center justify-center text-xl text-slate-500"
          >
            ✕
          </button>
        </div>

        <form onSubmit={(e) => void handleSubmit(e)} className="flex flex-col gap-3">
          <label htmlFor="plan-edit-per-day" className="text-xs font-medium text-slate-500">
            Max generations per day (blank = unlimited)
          </label>
          <input
            id="plan-edit-per-day"
            type="number"
            min={0}
            inputMode="numeric"
            value={maxPerDay}
            onChange={(e) => setMaxPerDay(e.target.value)}
            className="min-h-11 rounded-lg border border-slate-200 px-3 py-2.5 text-sm"
          />

          <label htmlFor="plan-edit-per-month" className="text-xs font-medium text-slate-500">
            Max generations per month (blank = unlimited)
          </label>
          <input
            id="plan-edit-per-month"
            type="number"
            min={0}
            inputMode="numeric"
            value={maxPerMonth}
            onChange={(e) => setMaxPerMonth(e.target.value)}
            className="min-h-11 rounded-lg border border-slate-200 px-3 py-2.5 text-sm"
          />

          <label htmlFor="plan-edit-timesteps" className="text-xs font-medium text-slate-500">
            Max diffusion steps (blank = unlimited)
          </label>
          <input
            id="plan-edit-timesteps"
            type="number"
            min={1}
            inputMode="numeric"
            value={maxTimesteps}
            onChange={(e) => setMaxTimesteps(e.target.value)}
            className="min-h-11 rounded-lg border border-slate-200 px-3 py-2.5 text-sm"
          />

          {error && (
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={isSubmitting}
            className="mt-1 rounded-full bg-indigo-600 px-4 py-3 font-semibold text-white active:scale-[0.98] disabled:opacity-50"
          >
            {isSubmitting ? "Saving…" : "Save changes"}
          </button>
        </form>
      </div>
    </div>
  );
}
