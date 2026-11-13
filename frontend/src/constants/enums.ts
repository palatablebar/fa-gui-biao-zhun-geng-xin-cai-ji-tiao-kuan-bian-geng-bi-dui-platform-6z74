import type {
  ChangeType,
  DocumentStatus,
  DiffRunStatus,
  Industry,
  RecalcDiffStatus,
  RecalcStatus,
  RunLineage,
  Severity,
} from '../types/domain'

/* 与 backend/src/constants/enums.py 一一对应（README 列出全部出现位置） */

export const CHANGE_TYPE_LABELS: Record<ChangeType, string> = {
  added: '新增',
  removed: '删除',
  modified: '修改',
  unchanged: '未变',
}

export const SEVERITY_LABELS: Record<Severity, string> = {
  major: '重大',
  general: '一般',
  editorial: '编辑性',
  unparsed: '解析失败',
}

export const SEVERITY_ORDER: Severity[] = ['major', 'general', 'editorial', 'unparsed']

export const SEVERITY_TONE: Record<Severity, string> = {
  major: 'seal',
  general: 'ochre',
  editorial: 'moss',
  unparsed: 'ink',
}

export const INDUSTRY_LABELS: Record<Industry, string> = {
  manufacturing: '制造',
  medical: '医疗',
  environmental: '环保',
}

export const INDUSTRY_ORDER: Industry[] = ['manufacturing', 'medical', 'environmental']

export const DOCUMENT_STATUS_LABELS: Record<DocumentStatus, string> = {
  current: '现行',
  superseded: '历史',
  collected: '待比对',
}

export const RUN_STATUS_LABELS: Record<DiffRunStatus, string> = {
  queued: '排队中',
  running: '比对中',
  succeeded: '已完成',
  failed: '失败',
}

export const RECALC_STATUS_LABELS: Record<RecalcStatus, string> = {
  queued: '排队中',
  running: '重算中',
  succeeded: '重算完成',
  failed: '重算失败',
}

export const RECALC_DIFF_LABELS: Record<RecalcDiffStatus, string> = {
  added: '新增',
  disappeared: '消失',
  regraded: '改级',
  unchanged: '不变',
}

export const RUN_LINEAGE_LABELS: Record<RunLineage, string> = {
  initial: '首次比对',
  recalc: '规则重算',
}

export const SOURCE_KIND_LABELS: Record<string, string> = {
  national_standard: '国家标准',
  regulatory: '部门规章',
  local_env: '地方文件',
}
