<template>
  <span class="rule-badge" :class="{ 'rule-badge--active': active }" :data-test="testId">
    <span class="rule-badge__seal">規</span>
    规则包 {{ version }}
    <span v-if="active" class="rule-badge__active">生效</span>
    <span v-else-if="superseded" class="rule-badge__superseded">历史口径</span>
  </span>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    version: string
    active?: boolean
    superseded?: boolean
    testId?: string
  }>(),
  { active: false, superseded: false, testId: 'rule-badge' },
)
</script>

<style scoped>
.rule-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-family: var(--sans);
  font-size: 10.5px;
  letter-spacing: 0.04em;
  color: var(--ink-soft);
  border: 1px solid var(--rule);
  background: var(--paper-2);
  padding: 2px 8px;
}
.rule-badge--active {
  color: var(--seal-deep);
  border-color: var(--seal);
}
.rule-badge__seal {
  font-family: var(--serif);
  color: var(--seal);
}
.rule-badge__active {
  color: var(--moss);
  border-left: 1px solid var(--rule-soft);
  padding-left: 6px;
}
.rule-badge__superseded {
  color: var(--ink-faint);
  border-left: 1px solid var(--rule-soft);
  padding-left: 6px;
}
</style>
