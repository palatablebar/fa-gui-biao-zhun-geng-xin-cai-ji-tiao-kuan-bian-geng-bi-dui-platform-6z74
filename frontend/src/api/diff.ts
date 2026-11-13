import http from './http'
import type {
  ChangeItem,
  DiffRun,
  Recalculation,
  RecalculationItem,
  RuleBundle,
} from '../types/domain'

export async function fetchRuns(
  documentKey?: string,
  activeOnly = false,
): Promise<{ items: DiffRun[] }> {
  const { data } = await http.get('/diffs', {
    params: {
      ...(documentKey ? { document_key: documentKey } : {}),
      ...(activeOnly ? { active_only: true } : {}),
    },
  })
  return data
}

export async function createDiff(
  baseVersionId: string,
  targetVersionId: string,
  ruleBundleVersion?: string,
): Promise<{ run: DiffRun }> {
  const { data } = await http.post('/diffs', {
    base_version_id: baseVersionId,
    target_version_id: targetVersionId,
    ...(ruleBundleVersion ? { rule_bundle_version: ruleBundleVersion } : {}),
  })
  return data
}

export async function fetchRun(runId: string): Promise<{ run: DiffRun }> {
  const { data } = await http.get(`/diffs/${runId}`)
  return data
}

export async function fetchRunChanges(
  runId: string,
  severity?: string,
): Promise<{ run: DiffRun; items: ChangeItem[]; total: number }> {
  const { data } = await http.get(`/diffs/${runId}/changes`, {
    params: severity ? { severity } : {},
  })
  return data
}

export async function fetchRuleBundles(): Promise<{
  items: RuleBundle[]
  current: string
}> {
  const { data } = await http.get('/rule-bundles')
  return data
}

export async function recalculateRun(
  runId: string,
  targetRuleBundleVersion: string,
): Promise<{ recalculation: Recalculation }> {
  const { data } = await http.post(`/diffs/${runId}/recalculate`, {
    target_rule_bundle_version: targetRuleBundleVersion,
  })
  return data
}

export async function fetchRecalculation(
  recalcId: string,
): Promise<{ recalculation: Recalculation; items: RecalculationItem[] }> {
  const { data } = await http.get(`/recalculations/${recalcId}`)
  return data
}

export async function fetchRecalculations(
  runId?: string,
): Promise<{ items: Recalculation[] }> {
  const { data } = await http.get('/recalculations', {
    params: runId ? { run_id: runId } : {},
  })
  return data
}
