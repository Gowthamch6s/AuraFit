export type Assessment = { assessment: {local_date:string;model_version:string;allostatic_load_index:number;recovery_score:number;overreach_risk_score:number;evidence:Record<string,unknown>};sufficient_data:boolean;disclaimer:string};
export type Targets = {targets:{local_date:string;step_target:number|null;active_energy_target_kcal:number|null;energy_budget_score:number|null;bedtime_start:string|null;bedtime_end:string|null;rationale:Record<string,unknown>};calorie_intake_target_kcal:null;disclaimer:string};
export type Forecast = {forecast_date:string;predicted_recovery_score:number|null;status:string;model_version:string|null;disclaimer:string};
export type Chat = {answer:string;status:string;citations:unknown[];disclaimer?:string};
export type TrendPoint={day:string;recovery:number;strain:number;risk:number};
export type Dashboard={assessment:Assessment;targets:Targets;forecast:Forecast;trend:TrendPoint[];demo:boolean};
