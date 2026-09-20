import type { TabDef } from '../components/Tabs'

// Shared by Sidebar.tsx (Metro) and VlineSidebar.tsx so both headers show
// the identical two tabs -- only `activeTabId` differs per page.
export const METRO_VLINE_NAV_TABS: TabDef[] = [
  { id: 'metro', label: 'Metro', to: '/' },
  { id: 'vline', label: 'V/Line', to: '/vline' },
]
