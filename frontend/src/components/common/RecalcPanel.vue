<template>
  <section class="recalc rise" data-test="recalc-panel">
    <header class="recalc__head">
      <div>
        <div class="eyebrow">rule recalculation · 口径重算</div>
        <h2 class="recalc__title">按新判级规则重算这对版本</h2>
        <p class="recalc__desc">
          重算会生成一份<strong>独立的新结论</strong>，当前确认过的旧结论原样保留；
          只有新结论与逐条对照完整落库后，统计与变更清单才一次性切换到新口径。
        </p>
      </div>
      <RuleBundleBadge
        :version="run.rule_bundle_version"
        :active="run.is_active"
        :superseded="!run.is_active"
        testId="recalc-current-rule"
      />
    </header>

    <!-- 当前批次的重算记录 -->
    <div v-if="recalcs.length" class="recalc__history">
      <div class="label">本批次重算记录</div>
      <ul>
        <li v-for="recalc in recalcs" :key="recalc.id" class="recalc__history-item">
          <button
            class="recalc__history-link"
            :class="{ 'is-on': selectedId === recalc.id }"
            :data-test="`recalc-history-${recalc.id}`"
            @click="select(recalc.id)"
          >
            {{ recalc.source_rule_bundle_version }} → {{ recalc.target_rule_bundle_version }}
            <span class="recalc__history-status" :class="`is-${recalc.status}`">
              {{ RECALC_STATUS_LABELS[recalc.status] }}
            </span>
          </button>
        </li>
      </ul>
    </div>

    <!-- 发起区 -->
    <div class="recalc__action">
      <label class="recalc__field">
        <span class="label">目标规则包</span>
        <select v-model="targetVersion" class="field" data-test="recalc-rule-select">
          <option
            v-for="bundle in bundles"
            :key="bundle.version"
            :value="bundle.version"
            :disabled="bundle.version === run.rule_bundle_version"
          >
            {{ bundle.version }}{{ bundle.is_current ? '（当前生效）' : '' }}
            {{ bundle.version === run.rule_bundle_version ? '· 与本批次同口径' : '' }}
          </option>
        </select>
      </label>
      <button
        class="btn btn--seal"
        :disabled="!canRecalc || busy"
        :title="canRecalc ? '按所选规则包生成对照新结论' : '没有可切换的其它规则包'"
        data-test="recalc-submit"
        @click="submit"
      >
        {{ busy ? '重算中…' : '按新口径重算' }}
      </button>
    </div>

    <p v-if="bundleHint" class="recalc__hint">{{ bundleHint }}</p>
    <p v-if="error" class="recalc__error">
      {{ error }}
      <RouterLink
        v-if="existingRunId"
        :to="`/diffs/${existingRunId}`"
        class="recalc__error-link"
        data-test="recalc-existing-link"
      >
        查看已有结论 →
      </RouterLink>
    </p>

    <!-- 重算结果摘要 + 逐条对照 -->
    <template v-if="detail">
      <div class="recalc__summary">
        <div class="recalc__count" v-for="status in DIFF_ORDER" :key="status">
          <span class="recalc__count-num num">{{ counts[status] ?? 0 }}</span>
          <span class="recalc__count-label" :class="`is-${status}`">{{ RECALC_DIFF_LABELS[status] }}</span>
        </div>
        <div class="recalc__count recalc__count--wide">
          <span class="recalc__count-num num">
            {{ detail.recalculation.diff_summary.old_change_count ?? '—' }}
            →
            {{ detail.recalculation.diff_summary.new_change_count ?? '—' }}
          </span>
          <span class="recalc__count-label">变更条数（旧 → 新）</span>
        </div>
      </div>

      <div class="recalc__table-wrap">
        <table class="recalc__table" data-test="recalc-diff-table">
          <thead>
            <tr>
              <th>对照</th>
              <th>条款（旧 → 新）</th>
              <th>旧口径</th>
              <th>新口径</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in detail.items"
              :key="row.id"
              :class="`recalc__row--${row.diff_status}`"
              :data-test="`recalc-row-${row.diff_status}`"
            >
              <td>
                <span class="recalc__tag" :class="`is-${row.diff_status}`">
                  {{ row.diff_status_label }}
                </span>
              </td>
              <td class="recalc__clause serif">
                {{ row.clause_label_old || '—' }} → {{ row.clause_label_new || '—' }}
              </td>
              <td>{{ formatLevel(row.old_change_type, row.old_severity) }}</td>
              <td>{{ formatLevel(row.new_change_type, row.new_severity) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="recalc__foot">
        <button class="btn btn--quiet" data-test="recalc-goto-new" @click="goNewRun">
          查看新口径批次（{{ detail.recalculation.target_rule_bundle_version }}）
        </button>
        <button class="btn btn--quiet" data-test="recalc-goto-old" @click="goOldRun">
          留在旧口径批次（{{ run.rule_bundle_version }}）
        </button>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import RuleBundleBadge from './RuleBundleBadge.vue'
import {
  RECALC_DIFF_LABELS,
  RECALC_STATUS_LABELS,
} from '../../constants/enums'
import {
  fetchRecalculation,
  fetchRecalculations,
  fetchRuleBundles,
  recalculateRun,
} from '../../api/diff'
import type {
  DiffRun,
  RecalcDiffStatus,
  Recalculation,
  RecalculationItem,
  RuleBundle,
} from '../../types/domain'
import type { ApiError } from '../../types/api'

const props = defineProps<{ run: DiffRun }>()
const router = useRouter()

const DIFF_ORDER: RecalcDiffStatus[] = ['regraded', 'added', 'disappeared', 'unchanged']

const bundles = ref<RuleBundle[]>([])
const current = ref('')
const recalcs = ref<Recalculation[]>([])
const selectedId = ref('')
const detail = ref<{ recalculation: Recalculation; items: RecalculationItem[] } | null>(null)
const busy = ref(false)
const error = ref('')
const existingRunId = ref('')

const targetVersion = ref('')
const selectableBundles = computed(() =>
  bundles.value.filter((bundle) => bundle.version !== props.run.rule_bundle_version),
)
const canRecalc = computed(() =>
  selectableBundles.value.some((bundle) => bundle.version === targetVersion.value),
)
const bundleHint = computed(() => {
  if (!bundles.value.length) return ''
  if (!selectableBundles.value.length) return '当前只有一个规则包，暂无可切换的新口径。'
  return ''
})
const counts = computed<Record<string, number>>(
  () => detail.value?.recalculation.diff_summary.diff_counts ?? {},
)

function formatLevel(changeType: string, severity: string): string {
  if (!changeType && !severity) return '—'
  const typeLabels: Record<string, string> = { added: '新增', removed: '删除', modified: '修改' }
  const sevLabels: Record<string, string> = {
    major: '重大',
    general: '一般',
    editorial: '编辑性',
    unparsed: '解析失败',
  }
  return `${typeLabels[changeType] ?? changeType} / ${sevLabels[severity] ?? severity}`
}

async function loadBundles() {
  const result = await fetchRuleBundles()
  bundles.value = result.items
  current.value = result.current
  const preferred =
    selectableBundles.value.find((bundle) => bundle.is_current) ?? selectableBundles.value[0]
  targetVersion.value = preferred?.version ?? ''
}

async function loadRecalcs(autoselect = false) {
  const result = await fetchRecalculations(props.run.id)
  recalcs.value = result.items
  if (autoselect && recalcs.value.length) {
    const succeeded = recalcs.value.find((item) => item.status === 'succeeded')
    if (succeeded) await select(succeeded.id)
  }
}

async function select(recalcId: string) {
  selectedId.value = recalcId
  detail.value = await fetchRecalculation(recalcId)
}

async function submit() {
  if (!targetVersion.value) return
  busy.value = true
  error.value = ''
  existingRunId.value = ''
  try {
    const result = await recalculateRun(props.run.id, targetVersion.value)
    await loadRecalcs(false)
    selectedId.value = result.recalculation.id
    if (result.recalculation.status === 'succeeded') {
      await select(result.recalculation.id)
    } else if (result.recalculation.status === 'failed') {
      error.value = result.recalculation.error_message || '重算失败，旧口径结论仍然有效。'
    } else {
      // worker 异步执行：轮询直到终态，期间旧口径结论保持生效。
      await pollRecalc(result.recalculation.id)
    }
  } catch (caught) {
    const apiError = caught as ApiError
    error.value = apiError.message ?? '重算发起失败'
    const runId = apiError.extra?.existing_run_id
    if (apiError.code === 'RULE_BUNDLE_EXISTS' && typeof runId === 'string') {
      existingRunId.value = runId
    }
  } finally {
    busy.value = false
  }
}

async function pollRecalc(recalcId: string, timeoutMs = 60000) {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    await new Promise((resolve) => setTimeout(resolve, 800))
    const latest = await fetchRecalculation(recalcId)
    detail.value = latest
    if (latest.recalculation.status === 'succeeded') {
      await loadRecalcs(false)
      return
    }
    if (latest.recalculation.status === 'failed') {
      error.value = latest.recalculation.error_message || '重算失败，旧口径结论仍然有效。'
      return
    }
  }
  error.value = '重算仍在执行，请稍后刷新本页查看结果；旧口径结论在切换前持续有效。'
}

function goNewRun() {
  const id = detail.value?.recalculation.new_run_id
  if (id) router.push(`/diffs/${id}`)
}
function goOldRun() {
  router.push(`/diffs/${props.run.id}`)
}

onMounted(async () => {
  await loadBundles()
  await loadRecalcs(true)
})
</script>

<style scoped>
.recalc {
  border: 1px solid var(--rule);
  background: var(--paper-2);
  border-top: 3px double var(--rule);
  padding: 18px 20px 20px;
  margin: 22px 0;
}
.recalc__head {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  align-items: flex-start;
}
.recalc__title {
  font-family: var(--serif);
  font-size: 17px;
  margin-top: 5px;
}
.recalc__desc {
  margin-top: 6px;
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--ink-soft);
  max-width: 72ch;
}
.recalc__history {
  margin-top: 14px;
}
.recalc__history ul {
  list-style: none;
  margin: 6px 0 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.recalc__history-link {
  font-family: var(--sans);
  font-size: 11px;
  border: 1px solid var(--rule);
  background: var(--paper);
  padding: 4px 10px;
  display: inline-flex;
  gap: 7px;
  align-items: center;
}
.recalc__history-link.is-on {
  border-color: var(--seal);
  color: var(--seal-deep);
}
.recalc__history-status {
  font-size: 10px;
}
.recalc__history-status.is-succeeded {
  color: var(--moss);
}
.recalc__history-status.is-failed {
  color: var(--seal);
}
.recalc__history-status.is-queued,
.recalc__history-status.is-running {
  color: var(--ochre);
}
.recalc__action {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  margin-top: 14px;
  flex-wrap: wrap;
}
.recalc__field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.recalc__field .field {
  min-width: 260px;
  padding: 6px 9px;
  font-size: 11.5px;
}
.recalc__hint {
  margin-top: 10px;
  font-size: 11px;
  color: var(--ink-faint);
}
.recalc__error {
  margin-top: 10px;
  font-size: 11.5px;
  color: var(--seal);
  border-left: 2px solid var(--seal);
  padding-left: 9px;
}
.recalc__error-link {
  color: var(--indigo);
  margin-left: 8px;
  border-bottom: 1px solid var(--indigo);
}
.recalc__summary {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  border-top: 1px solid var(--rule);
  border-bottom: 1px solid var(--rule);
  margin: 16px 0 14px;
}
.recalc__count {
  padding: 12px 10px;
  border-right: 1px dotted var(--rule-soft);
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.recalc__count:last-child {
  border-right: 0;
}
.recalc__count-num {
  font-size: 20px;
  font-weight: 600;
  font-family: var(--sans);
}
.recalc__count-label {
  font-size: 10.5px;
  color: var(--ink-faint);
}
.recalc__count-label.is-added {
  color: var(--moss);
}
.recalc__count-label.is-disappeared {
  color: var(--ink-faint);
}
.recalc__count-label.is-regraded {
  color: var(--seal);
}
.recalc__table-wrap {
  overflow-x: auto;
}
.recalc__table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11.5px;
}
.recalc__table th {
  text-align: left;
  font-family: var(--sans);
  font-size: 10px;
  letter-spacing: 0.12em;
  color: var(--ink-faint);
  border-bottom: 1px solid var(--rule);
  padding: 6px 8px;
}
.recalc__table td {
  padding: 8px;
  border-bottom: 1px solid var(--rule-soft);
  vertical-align: top;
}
.recalc__clause {
  white-space: nowrap;
}
.recalc__tag {
  font-size: 10px;
  padding: 2px 7px;
  border: 1px solid currentColor;
  font-family: var(--sans);
}
.recalc__tag.is-added {
  color: var(--moss);
}
.recalc__tag.is-disappeared {
  color: var(--ink-faint);
}
.recalc__tag.is-regraded {
  color: var(--seal);
}
.recalc__tag.is-unchanged {
  color: var(--indigo);
}
.recalc__foot {
  margin-top: 14px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
@media (max-width: 900px) {
  .recalc__summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
