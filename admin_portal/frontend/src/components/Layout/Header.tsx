import { useTranslation } from 'react-i18next';
import { useLocation } from 'react-router-dom';

export default function Header() {
  const { t, i18n } = useTranslation();
  const location = useLocation();

  const toggleLanguage = () => {
    const newLang = i18n.language === 'en' ? 'zh' : 'en';
    i18n.changeLanguage(newLang);
    localStorage.setItem('language', newLang);
  };

  // Get breadcrumb based on current path
  const getBreadcrumb = () => {
    const path = location.pathname;
    if (path === '/dashboard') return { section: 'Portal', page: t('nav.dashboard') };
    if (path === '/api-keys') return { section: 'Portal', page: t('nav.apiKeys') };
    if (path === '/pricing') return { section: 'Configuration', page: t('nav.pricing') };
    return { section: 'Portal', page: '' };
  };

  const breadcrumb = getBreadcrumb();

  return (
    <header className="flex items-center justify-between px-6 py-4 border-b border-border-dark bg-background-dark z-10">
      {/* Breadcrumbs */}
      <div className="flex items-center gap-4 text-white">
        <div className="md:hidden">
          <span className="material-symbols-outlined cursor-pointer">menu</span>
        </div>
        <div className="flex items-center gap-2 text-slate-400 text-sm">
          <span>{breadcrumb.section}</span>
          <span className="material-symbols-outlined text-[16px]">chevron_right</span>
          <span className="text-white font-medium">{breadcrumb.page}</span>
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2">
        {/* Language Toggle */}
        <button
          onClick={toggleLanguage}
          className="flex items-center gap-2 px-3 py-2 text-slate-400 hover:text-white hover:bg-surface-dark rounded-lg transition-colors text-sm"
        >
          <span className="material-symbols-outlined text-[18px]">language</span>
          <span className="hidden sm:inline">{i18n.language === 'en' ? 'EN' : '中文'}</span>
        </button>

        {/* Notifications */}
        <button className="relative p-2 text-slate-400 hover:text-white transition-colors rounded-lg hover:bg-surface-dark">
          <span className="material-symbols-outlined">notifications</span>
        </button>

        {/* Help */}
        <button className="p-2 text-slate-400 hover:text-white transition-colors rounded-lg hover:bg-surface-dark">
          <span className="material-symbols-outlined">help</span>
        </button>
      </div>
    </header>
  );
}
