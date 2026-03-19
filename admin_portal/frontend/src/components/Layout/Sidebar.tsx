import { NavLink, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../../hooks';

interface NavItem {
  path: string;
  icon: string;
  label: string;
  section?: string;
}

export default function Sidebar() {
  const { t } = useTranslation();
  const { logout } = useAuth();
  const location = useLocation();

  const navItems: NavItem[] = [
    { path: '/dashboard', icon: 'dashboard', label: t('nav.dashboard'), section: 'main' },
    { path: '/api-keys', icon: 'vpn_key', label: t('nav.apiKeys'), section: 'main' },
    { path: '/pricing', icon: 'payments', label: t('nav.pricing'), section: 'config' },
    { path: '/model-mapping', icon: 'swap_horiz', label: t('nav.modelMapping'), section: 'config' },
    { path: '/provider-keys', icon: 'key', label: 'Provider Keys', section: 'gateway' },
    { path: '/routing', icon: 'alt_route', label: 'Routing', section: 'gateway' },
    { path: '/failover', icon: 'sync_alt', label: 'Failover', section: 'gateway' },
  ];

  const isActive = (path: string) => location.pathname === path;

  return (
    <aside className="w-64 h-full hidden md:flex flex-col border-r border-border-dark bg-background-dark shrink-0">
      {/* Logo */}
      <div className="h-16 flex items-center px-6 border-b border-border-dark">
        <div className="flex items-center gap-2 text-primary">
          <span className="material-symbols-outlined text-3xl">hub</span>
          <span className="font-bold text-xl tracking-tight text-white">API Proxy</span>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-1">
        <div className="px-3 mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
          Main
        </div>
        {navItems
          .filter((item) => item.section === 'main')
          .map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors group ${
                isActive(item.path)
                  ? 'bg-primary/20 text-primary'
                  : 'text-slate-400 hover:text-white hover:bg-surface-dark'
              }`}
            >
              <span
                className={`material-symbols-outlined ${
                  isActive(item.path) ? 'text-primary fill-1' : 'group-hover:text-white'
                }`}
              >
                {item.icon}
              </span>
              <span className={`text-sm ${isActive(item.path) ? 'font-bold' : 'font-medium'}`}>
                {item.label}
              </span>
            </NavLink>
          ))}

        <div className="px-3 mt-6 mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
          Configuration
        </div>
        {navItems
          .filter((item) => item.section === 'config')
          .map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors group ${
                isActive(item.path)
                  ? 'bg-primary/20 text-primary'
                  : 'text-slate-400 hover:text-white hover:bg-surface-dark'
              }`}
            >
              <span
                className={`material-symbols-outlined ${
                  isActive(item.path) ? 'text-primary fill-1' : 'group-hover:text-white'
                }`}
              >
                {item.icon}
              </span>
              <span className={`text-sm ${isActive(item.path) ? 'font-bold' : 'font-medium'}`}>
                {item.label}
              </span>
            </NavLink>
          ))}

        {/* TODO: Gateway section hidden - pending optimization before release
        <div className="px-3 mt-6 mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
          Gateway
        </div>
        {navItems
          .filter((item) => item.section === 'gateway')
          .map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors group ${
                isActive(item.path)
                  ? 'bg-primary/20 text-primary'
                  : 'text-slate-400 hover:text-white hover:bg-surface-dark'
              }`}
            >
              <span
                className={`material-symbols-outlined ${
                  isActive(item.path) ? 'text-primary fill-1' : 'group-hover:text-white'
                }`}
              >
                {item.icon}
              </span>
              <span className={`text-sm ${isActive(item.path) ? 'font-bold' : 'font-medium'}`}>
                {item.label}
              </span>
            </NavLink>
          ))}
        */}
      </nav>

      {/* User Section */}
      <div className="p-4 border-t border-border-dark">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="size-8 rounded-full bg-gradient-to-tr from-primary to-purple-500"></div>
            <div className="flex flex-col overflow-hidden">
              <span className="text-sm font-medium text-white truncate">Admin</span>
            </div>
          </div>
          <button
            onClick={logout}
            className="p-2 text-slate-400 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-colors"
            title={t('nav.logout')}
          >
            <span className="material-symbols-outlined text-[20px]">logout</span>
          </button>
        </div>
      </div>
    </aside>
  );
}
