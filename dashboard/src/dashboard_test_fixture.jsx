import { Dashboard } from './App'

const admin = { username: 'test-admin', role: 'admin' }

export default function SignedInDashboard() {
  return <Dashboard user={admin} />
}
