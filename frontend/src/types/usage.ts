export interface UsageStatus {
  plan: string;
  limit_per_day: number | null;
  limit_per_month: number | null;
  used_today: number;
  used_this_month: number;
  remaining_today: number | null;
  remaining_this_month: number | null;
  max_num_timesteps: number | null;
}
