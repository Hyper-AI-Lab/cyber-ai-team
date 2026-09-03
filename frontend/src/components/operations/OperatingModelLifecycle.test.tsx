import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { actionClassKeys } from './ActionPolicyValidationPanel'
import { DomainLifecycleDetails } from './AutonomousCompanyPanel'

describe('operating-model lifecycle presentation', () => {
  it('derives action classes from the API response', () => {
    expect(actionClassKeys([
      { action_class: 'communications' },
      { action_class: 'internal_company_read' },
      { action_class: 'custom_partner_records' },
      { action_class: 'communications' },
    ])).toEqual([
      'communications',
      'custom_partner_records',
      'internal_company_read',
    ])
  })

  it('renders desired, effective, lifecycle, shadow, and recovery state', () => {
    const html = renderToStaticMarkup(createElement(DomainLifecycleDetails, {
      control: {
        desired_state: 'active',
        effective_state: 'paused',
        lifecycle_state: 'shadow',
        nonterminal_work_items: 3,
        backlog_limit: 20,
        shadow_progress: { successes: 2, required_successes: 3 },
        recovery_required: true,
      },
    }))

    expect(html).toContain('desired active')
    expect(html).toContain('effective paused')
    expect(html).toContain('lifecycle shadow')
    expect(html).toContain('shadow 2/3')
    expect(html).toContain('grounded recovery required')
  })
})
