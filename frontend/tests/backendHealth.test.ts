// Unit tests for the backend-connectivity policy.
//
//   node --test tests/backendHealth.test.ts
//
// Node's built-in test runner + type stripping are used, so no test framework
// or extra dependency is required.

import test from 'node:test'
import assert from 'node:assert/strict'

import {
  BackendHealthTracker,
  classifyRequestFailure,
  describeRequestFailure,
  type Reachability,
} from '../src/services/backendHealth.ts'

// ---- classification ---------------------------------------------------------

test('a server response of any status counts as reachable', () => {
  assert.equal(
    classifyRequestFailure({ response: { status: 200 } }),
    'reachable',
  )
  assert.equal(
    classifyRequestFailure({ response: { status: 500 } }),
    'reachable',
  )
})

test('a request with no response counts as unreachable', () => {
  assert.equal(
    classifyRequestFailure({ message: 'Network Error' }),
    'unreachable',
  )
  assert.equal(classifyRequestFailure(undefined), 'unreachable')
  assert.equal(
    classifyRequestFailure({ code: 'ECONNABORTED', message: 'timeout' }),
    'unreachable',
  )
})

test('a deliberately cancelled request carries no signal', () => {
  assert.equal(classifyRequestFailure({ code: 'ERR_CANCELED' }), 'canceled')
})

test('failure messages distinguish server errors from an unreachable backend', () => {
  assert.equal(
    describeRequestFailure(
      { response: { status: 500, data: { detail: 'boom' } } },
      'Could not load simulation jobs',
    ),
    'Could not load simulation jobs (HTTP 500: boom)',
  )
  assert.match(
    describeRequestFailure({ message: 'Network Error' }, 'Could not load simulation jobs'),
    /Backend unreachable/,
  )
})

// ---- latest-wins sequencing -------------------------------------------------

test('a successful jobs request marks the backend online', () => {
  const health = new BackendHealthTracker()
  assert.equal(health.isOnline, true)
  const seq = health.begin()
  assert.equal(health.settle(seq, 'reachable'), true)
})

test('an older failure cannot override a newer success', () => {
  const health = new BackendHealthTracker()
  const slow = health.begin() // older request, settles last
  const fast = health.begin() // newer request, settles first

  // The newer request succeeds: backend is up.
  assert.equal(health.settle(fast, 'reachable'), true)
  // The older request then fails; it must be ignored because it is stale.
  assert.equal(health.settle(slow, 'unreachable'), true)
  assert.equal(health.isOnline, true)
})

test('the newest failure does take effect', () => {
  const health = new BackendHealthTracker()
  assert.equal(health.settle(health.begin(), 'reachable'), true)
  assert.equal(health.settle(health.begin(), 'unreachable'), false)
  assert.equal(health.isOnline, false)
})

test('a cancelled request never changes connectivity', () => {
  const health = new BackendHealthTracker()
  assert.equal(health.settle(health.begin(), 'canceled'), true)
})

// ---- data-request policy (jobs endpoint) ------------------------------------

test('jobs returning HTTP 200 keeps the backend online', async () => {
  const health = new BackendHealthTracker()
  let probed = false
  const online = await health.settleDataRequest(
    health.begin(),
    'reachable',
    async () => {
      probed = true
      return 'reachable'
    },
  )
  assert.equal(online, true)
  assert.equal(probed, false, 'a working data request needs no health probe')
})

test('a transient jobs failure does not flip the backend offline when health is fine', async () => {
  const health = new BackendHealthTracker()
  let probes = 0
  const online = await health.settleDataRequest(
    health.begin(),
    'unreachable',
    async (): Promise<Reachability> => {
      probes += 1
      return 'reachable'
    },
  )
  assert.equal(online, true, 'one failing data route is not proof of downtime')
  assert.equal(probes, 1)
})

test('a genuinely unreachable backend goes offline', async () => {
  const health = new BackendHealthTracker()
  const online = await health.settleDataRequest(
    health.begin(),
    'unreachable',
    async () => 'unreachable',
  )
  assert.equal(online, false)
  assert.equal(health.isOnline, false)
})

test('a stale jobs failure neither flips the state nor triggers a probe', async () => {
  const health = new BackendHealthTracker()
  const stale = health.begin()
  health.settle(health.begin(), 'reachable') // newer request already succeeded
  let probes = 0
  const online = await health.settleDataRequest(stale, 'unreachable', async () => {
    probes += 1
    return 'unreachable'
  })
  assert.equal(online, true)
  assert.equal(probes, 0)
})

test('a later success recovers from an offline state', async () => {
  const health = new BackendHealthTracker()
  assert.equal(
    await health.settleDataRequest(health.begin(), 'unreachable', async () => 'unreachable'),
    false,
  )
  assert.equal(health.settle(health.begin(), 'reachable'), true)
})
