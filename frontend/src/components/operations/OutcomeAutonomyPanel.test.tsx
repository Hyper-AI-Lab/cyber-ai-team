import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import OutcomeAutonomyPanel from './OutcomeAutonomyPanel'

describe('OutcomeAutonomyPanel', () => {
  it('renders the evidence-to-outcome lifecycle and approval state', () => {
    const html = renderToStaticMarkup(
      createElement(OutcomeAutonomyPanel, {
        readiness: {
          company_signals: {
            status: 'ready',
            stale_pending: 0,
            undispositioned_processed: 0,
          },
          claim_extraction: {
            status: 'retrying',
            expired_leases: 0,
            scheduled_retries: 2,
          },
          outcome_learning: {
            status: 'processing',
            unassessed_work: 3,
            stale_unassessed_work: 0,
          },
        },
        modelCapabilities: {
          summary: {
            status: 'ready',
            qualified: 5,
            required: 5,
            items: [
              {
                task_type: 'observer_review',
                status: 'passed',
                provider: 'llama_cpp',
                model: 'qwen3-4b',
                score: 1,
                threshold: 0.8,
              },
            ],
          },
        },
        actionCandidates: [
          {
            id: 'candidate-1',
            tool_name: 'send_email',
            action_class: 'communications',
            agent_id: 'communications_agent',
            confidence: 0.91,
            status: 'approval_required',
            evidence_ids: ['evidence-1'],
            observer_review_id: 'observer-review-1',
            policy_decision: { source: 'opa' },
            approval_id: 'approval-1',
          },
        ],
        outcomes: [{ id: 'outcome-1' }],
        onNavigate: () => undefined,
      }),
    )

    expect(html).toContain('Evidence-to-Outcome Integrity')
    expect(html).toContain('5/5 task contracts qualified')
    expect(html).toContain('observer review')
    expect(html).toContain('send_email')
    expect(html).toContain('approval required')
    expect(html).toContain('Review 1 approval')
    expect(html).toContain('3 waiting')
  })

  it('explains genuinely empty action and capability state', () => {
    const html = renderToStaticMarkup(createElement(OutcomeAutonomyPanel, {
      readiness: {},
      modelCapabilities: null,
      actionCandidates: [],
      outcomes: [],
    }))

    expect(html).toContain('No model capability evaluation has been recorded.')
    expect(html).toContain('Domain agents have not found a justified action')
  })
})
