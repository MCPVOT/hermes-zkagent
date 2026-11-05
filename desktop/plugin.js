/**
 * zkagent-attest — the Desktop pane: the last attestation, read-only.
 *
 * What it shows is the public statement the receipt already publishes: verdict, action count,
 * total against the cap, execution id, and the trace root (copyable). Below that, a slot picker
 * reveals one action's digest and whether its inclusion path still reaches that root.
 *
 * Where the data comes from: `ctx.rest` → this package's own `dashboard/plugin_api.py`, which
 * runs in the gateway beside the tools that wrote `plugin-data/zkagent-attest/`. The SDK's file
 * door (`ctx.files`) cannot be used here — it is scoped to the folder the app owns for this
 * plugin and refuses Hermes' own data, so the runs are unreachable from the renderer by design.
 *
 * Plain ESM, loaded uncompiled: `jsx()` calls, and only `@hermes/plugin-sdk`, `react` and
 * `react/jsx-runtime` resolve. No DOM queries, no timers, no direct filesystem access.
 */

import {
  Badge,
  Button,
  CopyButton,
  EmptyState,
  fmtDateTime,
  GlyphSpinner,
  host,
  icons,
  Tip,
  cn,
  usePluginI18n,
  useQuery,
  useValue
} from '@hermes/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'zkagent-attest'

function Row({ label, children }) {
  return jsxs('div', {
    className: 'flex items-baseline justify-between gap-3',
    children: [
      jsx('span', { className: 'shrink-0 text-(--ui-text-tertiary)', children: label }),
      jsx('span', { className: 'min-w-0 truncate text-right tabular-nums text-(--ui-text-primary)', children })
    ]
  })
}

function shortRoot(root) {
  return typeof root === 'string' && root.length > 24 ? `${root.slice(0, 12)}…${root.slice(-8)}` : root
}

function VerdictBadge({ verdict }) {
  if (verdict !== 'accept' && verdict !== 'reject') {
    return jsx(Badge, { variant: 'muted', size: 'xs', children: '—' })
  }

  return jsx(Badge, { variant: verdict === 'accept' ? 'success' : 'destructive', size: 'xs', children: verdict.toUpperCase() })
}

function Recheck({ recheck, t }) {
  if (!recheck) return null

  if (recheck.available === false) {
    return jsx('div', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: t('recheckUnavailable', recheck.error || '') })
  }

  const verdict = recheck.verdict === 'accept' || recheck.verdict === 'reject' ? recheck.verdict : null
  // The stored header keeps the verdict text; only a live run of the binary can name the
  // machine-readable code, and a receipt that has outlived its policy re-checks as REJECT.
  const code = recheck.reason_code || recheck.reason || ''

  return jsxs('div', { className: 'flex items-center gap-1.5 text-[0.6875rem] text-(--ui-text-quaternary)', children: [
    jsx('span', { children: t('rechecked') }),
    jsx(VerdictBadge, { verdict }),
    code && jsx('span', { className: 'min-w-0 truncate', children: `· ${code}` })
  ] })
}

function Slots({ actions, disclosed, slot, onSelect, t }) {
  const buttons = []
  for (let index = 0; index < actions; index += 1) {
    const selected = slot === index
    const hasDisclosure = disclosed.includes(index)
    buttons.push(jsxs('button', {
      type: 'button',
      'aria-pressed': selected,
      'aria-label': t('slotLabel', index, hasDisclosure),
      title: t('slotLabel', index, hasDisclosure),
      onClick: () => onSelect(selected ? null : index),
      className: cn(
        'relative size-7 rounded-[3px] text-xs tabular-nums transition-colors',
        'text-(--ui-text-secondary) hover:bg-(--chrome-action-hover)',
        selected && 'bg-(--chrome-action-hover) text-(--ui-accent) shadow-[inset_0_0_0_1px_var(--ui-accent)]'
      ),
      children: [
        String(index),
        hasDisclosure && jsx('span', {
          'aria-hidden': true,
          className: 'absolute right-0.5 top-0.5 size-1 rounded-full bg-(--ui-accent)'
        })
      ]
    }, index))
  }

  return jsx('div', { className: 'flex flex-wrap items-center gap-1', role: 'group', 'aria-label': t('slotsLabel'), children: buttons })
}

function Disclosure({ ctx, run, scope, slot, t }) {
  const result = useQuery({
    queryKey: [ID, 'disclosure', scope, run.execution_id, slot],
    queryFn: () => ctx.rest(`/disclosure?index=${slot}`),
    enabled: slot !== null,
    retry: 0,
    staleTime: 2000
  })

  if (slot === null) {
    return jsx('div', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: t('pickSlot') })
  }

  if (result.isPending) {
    return jsx('div', { className: 'flex items-center gap-2 text-[0.6875rem] text-(--ui-text-tertiary)', children: [
      jsx(GlyphSpinner, { ariaLabel: t('checking') }),
      jsx('span', { children: t('checking') })
    ] })
  }

  if (result.isError || !result.data?.ok) {
    const message = result.data?.message || result.error?.message || t('checkFailed')
    return jsxs('div', { className: 'flex flex-col gap-1', children: [
      jsx('div', { className: 'text-[0.6875rem] text-(--ui-text-secondary)', children: message }),
      result.isError && jsx(Button, { variant: 'link', size: 'micro', onClick: () => void result.refetch(), children: t('retry') })
    ] })
  }

  const data = result.data
  return jsxs('div', { className: 'flex flex-col gap-1.5 rounded-[3px] bg-(--ui-bg-quaternary) p-2', children: [
    jsxs('div', { className: 'flex items-center gap-1.5', children: [
      jsx('span', { className: 'text-[0.6875rem] text-(--ui-text-tertiary)', children: t('slot', data.index) }),
      jsx('span', {
        className: 'min-w-0 flex-1 truncate font-mono text-[0.6875rem] text-(--ui-text-primary)',
        style: { fontFamily: 'var(--font-mono, monospace)' },
        title: data.digest,
        children: data.digest
      }),
      jsx(CopyButton, { appearance: 'icon', buttonSize: 'icon-xs', text: data.digest, label: t('copyDigest'), side: 'left' })
    ] }),
    jsxs('div', { className: 'flex items-center gap-1.5', children: [
      data.verified === true && jsx(VerdictBadge, { verdict: 'accept' }),
      data.verified === false && jsx(VerdictBadge, { verdict: 'reject' }),
      data.verified === null && jsx(Badge, { variant: 'muted', size: 'xs', children: t('notChecked') }),
      jsx('span', { className: 'min-w-0 flex-1 truncate text-[0.6875rem] text-(--ui-text-tertiary)', title: data.detail, children: data.detail || t('pathChecked') })
    ] })
  ] })
}

function Attestation({ ctx, data, scope, t }) {
  const run = data.run
  const [slot, setSlot] = useState(null)
  const actions = Number(run.actions) || 0
  const activeSlot = slot !== null && slot < actions ? slot : null
  const disclosed = Array.isArray(data.disclosed) ? data.disclosed : []

  return jsxs('div', { className: 'flex flex-col gap-3', children: [
    jsxs('div', { className: 'flex flex-col gap-1', children: [
      jsx(VerdictBadge, { verdict: run.verdict }),
      run.reason && jsx('div', { className: 'text-[0.6875rem] text-(--ui-text-secondary)', children: run.reason }),
      jsx(Recheck, { recheck: data.recheck, t })
    ] }),

    jsxs('div', { className: 'flex flex-col gap-1', children: [
      jsx(Row, { label: t('actionsLabel'), children: t('actions', actions, data.slots) }),
      jsx(Row, { label: t('spentLabel'), children: t('spent', run.total, run.cap) }),
      jsx(Row, { label: t('executionLabel'), children: run.execution_id ?? '—' }),
      jsx(Row, { label: t('whenLabel'), children: run.at ? fmtDateTime.format(new Date(run.at * 1000)) : '—' })
    ] }),

    jsxs('div', { className: 'flex flex-col gap-1', children: [
      jsx('div', { className: 'text-[0.6875rem] text-(--ui-text-tertiary)', children: t('traceRoot') }),
      jsxs('div', { className: 'flex items-center gap-1', children: [
        jsx('span', {
          className: 'min-w-0 flex-1 truncate text-[0.6875rem] text-(--ui-text-secondary)',
          style: { fontFamily: 'var(--font-mono, monospace)' },
          title: run.trace_root || '',
          children: shortRoot(run.trace_root) || '—'
        }),
        run.trace_root && jsx(CopyButton, {
          appearance: 'icon',
          buttonSize: 'icon-xs',
          text: run.trace_root,
          label: t('copyRoot'),
          side: 'left'
        })
      ] })
    ] }),

    jsx('div', { className: 'h-px bg-(--ui-stroke-secondary)' }),

    jsxs('div', { className: 'flex flex-col gap-2', children: [
      jsxs('div', { className: 'flex items-center justify-between gap-2', children: [
        jsx('span', { className: 'text-[0.6875rem] text-(--ui-text-tertiary)', children: t('discloseTitle') }),
        jsx('span', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: t('discloseNote') })
      ] }),
      jsx(Slots, { actions: Math.min(actions, Number(data.slots) || actions), disclosed, slot: activeSlot, onSelect: setSlot, t }),
      jsx(Disclosure, { ctx, run, scope, slot: activeSlot, t })
    ] })
  ] })
}

function ZkagentPane({ ctx }) {
  const t = usePluginI18n(ID)
  // ctx.rest is profile-scoped by construction, so the cache key has to be too: a profile
  // switch must not show another profile's receipt while the new one loads.
  const scope = useValue(host.state.profile)
  const latest = useQuery({
    queryKey: [ID, 'latest', scope],
    queryFn: () => ctx.rest('/latest'),
    // A receipt changes only when an attestation runs; a slow poll keeps the pane honest
    // without turning the panel into a load generator.
    refetchInterval: 15000,
    staleTime: 5000,
    retry: 1
  })

  const busy = latest.isFetching
  const header = jsxs('div', { className: 'flex items-center justify-between gap-2', children: [
    jsx('span', { className: 'text-[0.6875rem] font-medium uppercase tracking-wide text-(--ui-text-tertiary)', children: t('title') }),
    jsx(Tip, { label: t('refresh'), side: 'left', children: jsx(Button, {
      variant: 'ghost',
      size: 'icon-xs',
      'aria-label': t('refresh'),
      onClick: () => void latest.refetch(),
      children: jsx(icons.RefreshCw, { className: busy ? 'animate-spin' : undefined })
    }) })
  ] })

  let body
  if (latest.isPending) {
    body = jsx('div', { className: 'flex items-center gap-2 text-[0.6875rem] text-(--ui-text-tertiary)', children: [
      jsx(GlyphSpinner, { ariaLabel: t('loading') }),
      jsx('span', { children: t('loading') })
    ] })
  } else if (latest.isError) {
    body = jsxs('div', { className: 'flex flex-col items-start gap-1.5', children: [
      jsx('div', { className: 'text-[0.6875rem] font-medium text-(--ui-text-primary)', children: t('unreachable') }),
      jsx('div', { className: 'text-[0.6875rem] text-(--ui-text-tertiary)', children: latest.error?.message || '' }),
      jsx('div', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: t('backendHint') }),
      jsx(Button, { variant: 'link', size: 'micro', onClick: () => void latest.refetch(), children: t('retry') })
    ] })
  } else if (!latest.data?.attested) {
    body = jsx(EmptyState, {
      title: t('emptyTitle'),
      description: jsx('span', { children: t('emptyBody', latest.data?.pending_actions ?? 0) }),
      className: 'min-h-32'
    })
  } else {
    body = jsx(Attestation, { ctx, data: latest.data, scope, t })
  }

  return jsxs('div', { className: 'flex h-full min-h-0 flex-col gap-3 overflow-y-auto p-3', children: [header, body] })
}

export default {
  id: ID,
  name: 'zkagent receipt',
  register(ctx) {
    ctx.i18n.register({
      en: {
        title: 'zkagent receipt',
        refresh: 'Re-read the latest attestation',
        loading: 'Reading the latest receipt',
        unreachable: 'The receipt backend did not answer',
        backendHint: 'The pane reads through this package\'s plugin_api.py — enable the plugin and restart the gateway if it stays silent.',
        retry: 'Try again',
        emptyTitle: 'No attestation yet',
        emptyBody: pending => `${pending} action(s) recorded. Run zkagent_attest to sign a policy and prove this session.`,
        actionsLabel: 'Actions',
        actions: (n, slots) => `${n} of ${slots} slots`,
        spentLabel: 'Total',
        spent: (total, cap) => `${total} / ${cap} units`,
        executionLabel: 'Execution',
        whenLabel: 'Completed',
        traceRoot: 'Trace root',
        copyRoot: 'Copy the trace root',
        copyDigest: 'Copy the disclosed digest',
        rechecked: 're-checked now:',
        recheckUnavailable: error => `live re-check unavailable — ${error}`,
        discloseTitle: 'Disclose one action',
        discloseNote: 'one action per receipt',
        slotsLabel: 'Action slots',
        slotLabel: (index, disclosed) => `Slot ${index}${disclosed ? ' (already disclosed)' : ''}`,
        pickSlot: 'Pick a slot to see whether its inclusion path still verifies.',
        slot: index => `slot ${index}`,
        checking: 'Verifying the inclusion path',
        checkFailed: 'The disclosure could not be read',
        notChecked: 'NOT CHECKED',
        pathChecked: 'path reaches the trace root'
      }
    })

    ctx.register({
      id: 'attestation',
      area: 'panes',
      title: 'zkagent receipt',
      data: { placement: 'right', width: '300px' },
      render: () => jsx(ZkagentPane, { ctx })
    })
  }
}
