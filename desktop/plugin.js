/**
 * treg-jobs — desktop half. Ships inside the agent package as `desktop/plugin.js`
 * (the app copies it to <hermes home>/desktop-plugins/treg-jobs/). Plain ESM, loaded
 * uncompiled: the UI is jsx() calls, not JSX syntax. Only `@hermes/plugin-sdk`,
 * `react` and `react/jsx-runtime` resolve here.
 *
 * Everything it draws comes from the Python half's own namespace (`api.rest('/…')` →
 * /api/plugins/treg-jobs/…), so the pane reads the same SQLite state the tools write:
 * the pipeline, who to write to, and what treg calls have cost. Nothing here spends
 * money — the only actions are a local status move and opening a posting in the browser.
 */

import {
  Badge,
  Button,
  cn,
  EmptyState,
  ErrorState,
  haptic,
  host,
  KEYBINDS_AREA,
  PALETTE_AREA,
  PANES_AREA,
  queryClient,
  ROUTES_AREA,
  ScrollArea,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  SIDEBAR_NAV_AREA,
  STATUSBAR_AREAS,
  Tip,
  useMutation,
  useQuery
} from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'treg-jobs'
const ROUTE = '/treg-jobs'

// Set in register(ctx); components only render after it.
let api = null

// Column order and labels; the statuses themselves come from the backend rows, so a
// status the Python side renames shows up as its own column rather than vanishing.
const COLUMNS = [
  { key: 'saved', label: 'Saved' },
  { key: 'researching', label: 'Researching' },
  { key: 'applied', label: 'Applied' },
  { key: 'interview', label: 'Interview' },
  { key: 'offer', label: 'Offer' },
  { key: 'rejected', label: 'Rejected' },
  { key: 'skipped', label: 'Skipped' }
]

const NEXT_STATUS = {
  saved: 'researching',
  researching: 'applied',
  applied: 'interview',
  interview: 'offer',
  offer: 'offer',
  rejected: 'saved',
  skipped: 'saved'
}

function refresh() {
  queryClient.invalidateQueries({ queryKey: [ID, 'state'] })
  queryClient.invalidateQueries({ queryKey: [ID, 'job'] })
}

function useJobsState() {
  return useQuery({
    queryKey: [ID, 'state'],
    queryFn: () => api.rest('/state'),
    refetchInterval: 5000
  })
}

function useMove() {
  return useMutation({
    mutationFn: ({ id, status, person }) =>
      api.rest('/move', { method: 'POST', body: { id, status, person } }),
    onSuccess: () => {
      haptic('tap')
      refresh()
    }
  })
}

function money(usd) {
  const n = Number(usd || 0)
  if (!n) return '$0'
  // three decimals read as thousands to a human: $2.000 looks like two thousand dollars.
  if (n < 1) return `$${n.toFixed(3)}`
  return `$${n.toFixed(2)}`
}

function SpendHeader({ state }) {
  const spend = state?.spend || {}
  const left = Number(spend.left_usd || 0)
  const total = Number(spend.budget_usd || 0)
  const usedRatio = total > 0 ? Math.min(1, 1 - left / total) : 0
  return jsxs('div', {
    className: 'flex flex-col gap-1.5 border-b border-(--ui-stroke-secondary) px-3 py-2',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx('span', { className: 'text-[13px] font-medium', children: 'Jobs' }),
          jsx('span', {
            className: 'shrink-0 rounded border border-(--ui-stroke-secondary) px-1 text-[9px] text-(--ui-text-quaternary)',
            children: '0.3.5'
          }),
          jsx(Badge, { children: `${state?.tracked ?? 0} tracked` }),
          jsx('span', { className: 'min-w-0 flex-1' }),
          jsx(Tip, {
            label: 'Spent on treg calls this session, and what is left of the budget',
            children: jsxs('span', {
              className: 'font-mono text-[11px] text-(--ui-text-tertiary)',
              children: [`${money(spend.spent_usd)} spent`, ` · ${money(left)} left`]
            })
          })
        ]
      }),
      jsx('div', {
        className: 'h-1 w-full overflow-hidden rounded-full bg-(--ui-stroke-secondary)',
        children: jsx('div', {
          className: 'h-full bg-(--ui-accent) transition-all',
          style: { width: `${Math.round(usedRatio * 100)}%` }
        })
      }),
      state?.key_present
        ? jsx('span', {
            className: 'text-[11px] text-(--ui-text-quaternary)',
            children: `${state?.jobs_cached ?? 0} postings cached locally`
          })
        : jsx('span', {
            className: 'text-[11px] text-(--ui-accent)',
            children: 'No TREG_TOKEN — set the treg team key to call the API'
          })
    ]
  })
}

function StatusSelect({ row, move }) {
  // Every stage in one control: no clicking through the chain to reach rejected.
  return jsxs(Select, {
    value: row.status,
    onValueChange: value => move.mutate({ id: row.job_id, status: value }),
    children: [
      jsx(SelectTrigger, {
        className: 'h-6 w-[112px] shrink-0 text-[11px]',
        children: jsx(SelectValue, {})
      }),
      jsx(SelectContent, {
        children: ['saved', 'researching', 'applied', 'interview', 'offer', 'rejected', 'skipped'].map(stage =>
          jsx(SelectItem, { value: stage, className: 'text-[12px]', children: stage }, stage)
        )
      })
    ]
  })
}

function ProfileBar({ state }) {
  const profile = state?.profile || {}
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (editing) setDraft(String(profile.summary || ''))
  }, [editing])

  const save = async () => {
    setSaving(true)
    try {
      await api.rest('/profile', { method: 'POST', body: { profile: { ...profile, summary: draft } } })
      refresh()
      setEditing(false)
    } finally {
      setSaving(false)
    }
  }

  return jsxs('div', {
    className: 'flex flex-col gap-1 border-b border-(--ui-stroke-secondary) px-3 py-2',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-1.5',
        children: [
          jsx('span', { className: 'shrink-0 text-[11px] text-(--ui-text-tertiary)', children: 'Profile' }),
          jsx('span', {
            className: 'min-w-0 flex-1 truncate text-[11px] text-(--ui-text-quaternary)',
            title: String(profile.summary || ''),
            children: profile.summary ? String(profile.summary) : 'none — paste a resume in chat, or write it here'
          }),
          jsx('span', {
            className: 'shrink-0',
            children: jsx(Button, {
              size: 'sm',
              variant: 'ghost',
              onClick: () => setEditing(v => !v),
              children: editing ? 'Cancel' : 'Edit'
            })
          })
        ]
      }),
      editing
        ? jsxs('div', {
            className: 'flex flex-col gap-1',
            children: [
              jsx('textarea', {
                value: draft,
                onChange: e => setDraft(e.target.value),
                rows: 4,
                placeholder: 'Senior backend engineer, 6 years Go and Postgres at fintech startups, remote EU, min 80k',
                className:
                  'w-full resize-y rounded border border-(--ui-stroke-secondary) bg-transparent px-2 py-1 text-[11px] outline-none'
              }),
              jsxs('div', {
                className: 'flex items-center justify-end gap-1.5',
                children: [
                  jsx(Button, { size: 'sm', variant: 'ghost', onClick: save, children: saving ? 'Saving…' : 'Save' })
                ]
              })
            ]
          })
        : null
    ]
  })
}

function JobRow({ row, move, dense }) {
  const next = NEXT_STATUS[row.status] || 'applied'
  // Stacked, not squeezed: title clamps to two lines, meta is one ellipsised line, and
  // the actions row carries its own buttons right next to the badge. Nothing is pushed
  // to a right edge, so a narrow pane cannot hide an action.
  const actions = []
  actions.push(jsx('span', { className: 'shrink-0', children: jsx(Badge, { children: row.status }) }, 'badge'))
  if (row.person) {
    actions.push(
      jsx(
        'span',
        {
          className: 'min-w-0 max-w-[38%] truncate text-[11px] text-(--ui-text-quaternary)',
          title: row.person,
          children: row.person
        },
        'person'
      )
    )
  }
  actions.push(jsx(StatusSelect, { row, move }, 'move'))
  if (row.url) {
    actions.push(
      jsx(
        'span',
        {
          className: 'shrink-0',
          children: jsx(Button, {
            size: 'sm',
            variant: 'ghost',
            onClick: () => api.os.openExternal(row.url),
            children: 'Open'
          })
        },
        'open'
      )
    )
  }

  return jsxs('div', {
    className: cn(
      'group flex w-full min-w-0 flex-col gap-1 overflow-hidden border-b border-(--ui-stroke-secondary) px-3 py-2',
      dense ? '' : 'hover:bg-(--ui-stroke-secondary)/40'
    ),
    children: [
      jsx('div', {
        className: 'min-w-0 text-[13px] font-medium leading-snug',
        style: { display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' },
        title: row.title,
        children: row.title || '(untitled role)'
      }),
      jsx('div', {
        className: 'min-w-0 truncate text-[11px] text-(--ui-text-tertiary)',
        title: `${row.company || ''}${row.location ? ` · ${row.location}` : ''}`,
        children: `${row.company || '—'}${row.location ? ` · ${row.location}` : ''}`
      }),
      jsxs('div', { className: 'flex min-w-0 flex-wrap items-center gap-1.5', children: actions })
    ]
  })
}

function JobsPane() {
  const query = useJobsState()
  const move = useMove()
  const state = query.data

  if (query.isError) {
    return jsx(ErrorState, {
      title: 'Jobs backend unreachable',
      description: String(query.error?.message || query.error)
    })
  }
  if (state && state.ok === false) {
    return jsx(ErrorState, { title: 'Jobs backend error', description: String(state.error) })
  }

  const pipeline = state?.pipeline || {}
  const rows = COLUMNS.flatMap(c => pipeline[c.key] || [])
  const header = jsx(SpendHeader, { state: state || {} })

  if (!rows.length) {
    return jsxs('div', {
      className: 'flex h-full min-h-0 flex-col',
      children: [
        header,
        jsx('div', {
          className: 'flex-1 p-3',
          children: jsx(EmptyState, {
            title: 'Nothing tracked yet',
            description:
              'Ask the agent for a search — jobs_search finds live roles, jobs_people finds who owns one — then save a role and it appears here.'
          })
        })
      ]
    })
  }

  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col',
    children: [
      header,
      jsx(ProfileBar, { state: state || {} }),
      jsx(ScrollArea, {
        className: 'min-h-0 flex-1',
        children: jsx('div', {
          className: 'flex flex-col',
          children: rows.map(row => jsx(JobRow, { row, move, dense: true }, row.job_id))
        })
      })
    ]
  })
}

function JobsPage() {
  const query = useJobsState()
  const move = useMove()
  const state = query.data

  if (query.isError) {
    return jsx(ErrorState, {
      title: 'Jobs backend unreachable',
      description: String(query.error?.message || query.error)
    })
  }

  const pipeline = state?.pipeline || {}

  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col',
    children: [
      jsx(SpendHeader, { state: state || {} }),
      jsx(ScrollArea, {
        className: 'min-h-0 flex-1',
        children: jsx('div', {
          className: 'flex gap-3 p-3',
          children: COLUMNS.map(column => {
            const rows = pipeline[column.key] || []
            return jsxs(
              'div',
              {
                className: 'flex w-64 shrink-0 flex-col gap-2',
                children: [
                  jsxs('div', {
                    className: 'flex items-center gap-2',
                    children: [
                      jsx('span', {
                        className: 'text-[12px] font-medium uppercase tracking-wide text-(--ui-text-tertiary)',
                        children: column.label
                      }),
                      jsx(Badge, { children: `${rows.length}` })
                    ]
                  }),
                  jsx('div', {
                    className: 'flex flex-col rounded-md border border-(--ui-stroke-secondary)',
                    children: rows.length
                      ? rows.map(row => jsx(JobRow, { row, move }, row.job_id))
                      : jsx('div', {
                          className: 'px-3 py-6 text-center text-[11px] text-(--ui-text-quaternary)',
                          children: 'empty'
                        })
                  })
                ]
              },
              column.key
            )
          })
        })
      })
    ]
  })
}

function JobsChip() {
  const query = useJobsState()
  const state = query.data
  const spend = Number(state?.spend?.spent_usd || 0)
  const tracked = Number(state?.tracked || 0)
  return jsx(Tip, {
    label: `${tracked} tracked · ${money(spend)} spent on treg calls`,
    children: jsxs('span', {
      className: 'flex cursor-pointer items-center gap-1.5 text-[11px] text-(--ui-text-tertiary)',
      onClick: () => host.navigate(ROUTE),
      children: [
        jsx('span', { className: 'font-medium', children: 'jobs' }),
        jsx('span', {
          className: tracked ? 'text-(--ui-text-secondary)' : 'text-(--ui-text-quaternary)',
          children: `${tracked}`
        }),
        jsx('span', { className: 'text-(--ui-text-quaternary)', children: money(spend) })
      ]
    })
  })
}

export default {
  id: ID, // must match the folder name
  name: 'Treg Jobs',
  // The Python half is already gated by plugins.enabled; asking for a second toggle to
  // see the pane is a step nobody finds.
  defaultEnabled: true,
  register(ctx) {
    api = ctx

    ctx.register({
      id: 'pane',
      area: PANES_AREA,
      title: 'Jobs',
      data: { placement: 'right', width: '360px', dock: { pane: 'workspace', pos: 'right' } },
      render: () => jsx(JobsPane, {})
    })

    ctx.register({
      id: 'chip',
      area: STATUSBAR_AREAS.right,
      order: 130,
      render: () => jsx(JobsChip, {})
    })

    ctx.registerMany([
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        data: { path: ROUTE, label: 'Jobs', codicon: 'briefcase' }
      },
      {
        id: 'route',
        area: ROUTES_AREA,
        data: { path: ROUTE },
        render: () => jsx(JobsPage, {})
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'treg-jobs.open',
          label: 'Jobs: open the pipeline',
          keywords: ['jobs', 'pipeline', 'applications', 'treg-jobs'],
          run: () => host.navigate(ROUTE)
        }
      },
      {
        id: 'reveal',
        area: PALETTE_AREA,
        data: {
          id: 'treg-jobs.reveal',
          label: 'Jobs: show the pane beside the chat',
          keywords: ['jobs', 'pane', 'sidebar', 'treg-jobs'],
          run: () => ctx.runAction('view.toggleRightSidebar')
        }
      },
      {
        id: 'hotkey',
        area: KEYBINDS_AREA,
        data: {
          id: 'treg-jobs.open',
          label: 'Jobs: open the pipeline',
          category: 'Jobs',
          defaults: ['mod+alt+j'],
          run: () => host.navigate(ROUTE)
        }
      }
    ])
  }
}
