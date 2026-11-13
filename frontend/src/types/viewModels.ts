export type NextActionKind = 'continue' | 'retest' | 'plan' | 'weakness' | 'import' | 'quick'

export interface NextAction {
  kind: NextActionKind
  title: string
  description: string
  actionLabel: string
  to: string
  priority: number
  eyebrow?: string
}

export interface PaperSummary {
  paper_id: string
  title: string
  mode: 'practice' | 'quiz' | string
  question_count: number
  created_at?: string
  active_attempt_id?: string | null
  latest_attempt_id?: string | null
}

export interface AttemptSummary {
  attempt_id: string
  paper_id: string
  paper_title?: string
  status: 'in_progress' | 'submitted' | string
  mode?: 'practice' | 'quiz' | string
  score?: number | null
  max_score?: number | null
  started_at?: string
  submitted_at?: string | null
  diagnosis_status?: 'pending' | 'running' | 'completed' | 'failed' | 'not_started' | string
}

export interface KnowledgeStateView {
  kp_id: string
  kp_name: string
  chapter?: string | null
  mastery?: number | null
  uncertainty?: number | null
  evidence_count?: number
  status_label?: string
  updated_at?: string
}

export interface DiagnosisView {
  run_id?: string
  target_id?: string
  status: string
  status_label: string
  summary: string
  strengths: string[]
  weaknesses: string[]
  suggestions: string[]
  created_at?: string
  raw?: unknown
}

export interface NextActionInput {
  activeAttempt?: { attempt_id: string; paper_title?: string } | null
  dueRetests?: Array<{ task_id?: string; kp_id?: string; kp_name?: string; title?: string }>
  todayPlans?: Array<{ task_id?: string; title?: string; status?: string }>
  weakStates?: Array<{ kp_id: string; kp_name?: string; mastery?: number | null }>
  hasMaterials?: boolean
}
