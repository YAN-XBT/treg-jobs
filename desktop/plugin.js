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
  SIDEBAR_NAV_AREA,
  STATUSBAR_AREAS,
  Tip,
  useMutation,
  useQuery
} from '@hermes/plugin-sdk'
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

function JobRow({ row, move, dense }) {
  const next = NEXT_STATUS[row.status] || 'applied'
  // Nothing here may shrink an action off the edge: a narrow pane used to push the
  // buttons out of the card entirely. Text truncates, actions never do, and the meta
  // row wraps instead of overflowing.
  return jsxs('div', {
    className: cn(
      'group flex min-w-0 flex-col gap-1 overflow-hidden border-b border-(--ui-stroke-secondary) px-3 py-2',
      dense ? '' : 'hover:bg-(--ui-stroke-secondary)/40'
    ),
    children: [
      jsxs('div', {
        className: 'flex min-w-0 items-start gap-2',
        children: [
          jsx('span', {
            className: 'min-w-0 flex-1 truncate text-[13px] font-medium',
            title: row.title,
            children: row.title || '(untitled role)'
          }),
          row.url
            ? jsx('span', {
                className: 'shrink-0',
                children: jsx(Button, {
                  size: 'sm',
                  variant: 'ghost',
                  onClick: () => api.os.openExternal(row.url),
                  children: 'Open'
                })
              })
            : null
        ]
      }),
      jsx('div', {
        className: 'min-w-0 truncate text-[11px] text-(--ui-text-tertiary)',
        title: `${row.company || ''}${row.location ? ` · ${row.location}` : ''}`,
        children: `${row.company || '—'}${row.location ? ` · ${row.location}` : ''}`
      }),
      jsxs('div', {
        className: 'flex min-w-0 flex-wrap items-center gap-1.5',
        children: [
          jsx('span', { className: 'shrink-0', children: jsx(Badge, { children: row.status }) }),
          row.person
            ? jsx('span', {
                className: 'min-w-0 max-w-[45%] truncate text-[11px] text-(--ui-text-quaternary)',
                title: row.person,
                children: row.person
              })
            : null,
          jsx('span', {
            className: 'ml-auto shrink-0',
            children: jsx(Button, {
              size: 'sm',
              variant: 'ghost',
              onClick: () => move.mutate({ id: row.job_id, status: next }),
              children: `→ ${next}`
            })
          })
        ]
      })
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
