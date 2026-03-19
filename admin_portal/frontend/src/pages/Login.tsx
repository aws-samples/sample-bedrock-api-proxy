import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../hooks';

export default function Login() {
  const { t, i18n } = useTranslation();
  const {
    login,
    completeNewPassword,
    loading,
    error,
    isNewPasswordRequired,
    sessionExpired,
    clearSessionExpired,
    resetPasswordState,
    initiateResetPassword,
    completeResetPassword,
    cancelResetPassword,
  } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showNewPassword, setShowNewPassword] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [showForgotPassword, setShowForgotPassword] = useState(false);
  const [resetEmail, setResetEmail] = useState('');
  const [resetCode, setResetCode] = useState('');
  const [resetNewPassword, setResetNewPassword] = useState('');
  const [resetConfirmPassword, setResetConfirmPassword] = useState('');

  // Auto-clear session expired message after 5 seconds
  useEffect(() => {
    if (sessionExpired) {
      const timer = setTimeout(() => {
        clearSessionExpired();
      }, 5000);
      return () => clearTimeout(timer);
    }
  }, [sessionExpired, clearSessionExpired]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPasswordError(null);
    clearSessionExpired(); // Clear any session expired message on login attempt
    await login(username, password);
  };

  const handleNewPasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPasswordError(null);

    // Validate password match
    if (newPassword !== confirmPassword) {
      setPasswordError(t('auth.passwordMismatch'));
      return;
    }

    // Validate password requirements
    if (newPassword.length < 10) {
      setPasswordError(t('auth.passwordTooShort'));
      return;
    }

    await completeNewPassword(newPassword);
  };

  const toggleLanguage = () => {
    const newLang = i18n.language === 'en' ? 'zh' : 'en';
    i18n.changeLanguage(newLang);
    localStorage.setItem('language', newLang);
  };

  // Handle forgot password - request code
  const handleForgotPasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPasswordError(null);
    await initiateResetPassword(resetEmail);
  };

  // Handle reset password - submit code and new password
  const handleResetPasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPasswordError(null);

    // Validate password match
    if (resetNewPassword !== resetConfirmPassword) {
      setPasswordError(t('auth.passwordMismatch'));
      return;
    }

    // Validate password requirements
    if (resetNewPassword.length < 10) {
      setPasswordError(t('auth.passwordTooShort'));
      return;
    }

    const success = await completeResetPassword(resetCode, resetNewPassword);
    if (success) {
      // Reset form fields after successful password reset
      setResetEmail('');
      setResetCode('');
      setResetNewPassword('');
      setResetConfirmPassword('');
    }
  };

  // Handle back to login from reset password flow
  const handleBackToLogin = () => {
    setShowForgotPassword(false);
    setResetEmail('');
    setResetCode('');
    setResetNewPassword('');
    setResetConfirmPassword('');
    setPasswordError(null);
    cancelResetPassword();
  };

  return (
    <div className="relative flex h-full min-h-screen w-full flex-col bg-background-dark overflow-x-hidden">
      {/* Header */}
      <header className="absolute top-0 left-0 w-full z-20 px-6 py-6 flex justify-between items-center">
        {/* Logo */}
        <div className="flex items-center gap-3 text-white">
          <div className="size-10 bg-primary/10 rounded-xl flex items-center justify-center border border-primary/20 text-primary shadow-[0_0_15px_rgba(43,108,238,0.15)]">
            <span className="material-symbols-outlined" style={{ fontSize: '24px' }}>
              transform
            </span>
          </div>
          <h2 className="text-white text-lg font-bold leading-tight tracking-[-0.015em] hidden sm:block">
            anthropic_api_proxy
          </h2>
        </div>

        {/* Language Switcher */}
        <button
          onClick={toggleLanguage}
          className="group flex items-center justify-center overflow-hidden rounded-lg h-10 px-4 bg-surface-dark border border-border-dark text-white text-sm font-medium hover:border-primary/50 hover:text-primary transition-all duration-200 shadow-sm"
        >
          <span className="material-symbols-outlined mr-2 text-[18px] text-gray-400 group-hover:text-primary">
            language
          </span>
          <span className="truncate">{t('language.en')} / {t('language.zh')}</span>
        </button>
      </header>

      {/* Main Content */}
      <div className="layout-container flex h-full grow flex-col items-center justify-center p-4 relative">
        {/* Background Glow */}
        <div className="absolute pointer-events-none inset-0 flex items-center justify-center overflow-hidden">
          <div className="w-[600px] h-[600px] bg-primary/5 rounded-full blur-[100px] absolute -translate-y-1/4"></div>
        </div>

        {/* Login Card */}
        <div className="relative w-full max-w-[440px] flex flex-col bg-surface-dark border border-border-dark rounded-2xl shadow-2xl z-10 overflow-hidden">
          {/* Decorative Top Bar */}
          <div className="h-1 w-full bg-gradient-to-r from-primary via-blue-400 to-primary"></div>

          <div className="p-8 pb-6">
            {/* Session Expired Banner */}
            {sessionExpired && (
              <div className="flex items-center gap-3 text-amber-400 text-sm bg-amber-500/10 p-4 rounded-lg border border-amber-500/20 mb-6 animate-pulse">
                <span className="material-symbols-outlined text-[20px]">schedule</span>
                <div className="flex-1">
                  <p className="font-medium">{t('auth.sessionExpired', 'Session Expired')}</p>
                  <p className="text-amber-400/70 text-xs mt-0.5">{t('auth.sessionExpiredMessage', 'Your session has expired. Please login again.')}</p>
                </div>
                <button
                  onClick={clearSessionExpired}
                  className="text-amber-400/70 hover:text-amber-400 transition-colors"
                >
                  <span className="material-symbols-outlined text-[18px]">close</span>
                </button>
              </div>
            )}

            {/* Header Section */}
            <div className="flex flex-col gap-2 mb-8">
              <div className="flex items-center gap-2 text-primary mb-2">
                <span className="material-symbols-outlined">admin_panel_settings</span>
                <span className="text-xs font-bold uppercase tracking-wider text-primary/80">
                  {t('auth.secureAccess')}
                </span>
              </div>
              <h1 className="text-white tracking-tight text-3xl font-bold leading-tight">
                {t('auth.adminPortal')}
              </h1>
              <p className="text-gray-400 text-base font-normal leading-normal">
                {isNewPasswordRequired
                  ? t('auth.setNewPassword')
                  : t('auth.enterCredentials')}
              </p>
            </div>

            {/* New Password Form (First Login) */}
            {isNewPasswordRequired ? (
              <form onSubmit={handleNewPasswordSubmit} className="flex flex-col gap-5">
                {/* Password Requirements */}
                <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-3">
                  <p className="text-blue-400 text-xs font-medium mb-2">{t('auth.passwordRequirements')}</p>
                  <ul className="text-gray-400 text-xs space-y-1">
                    <li className="flex items-center gap-2">
                      <span className="material-symbols-outlined text-[14px]">check_circle</span>
                      {t('auth.passwordMin10')}
                    </li>
                    <li className="flex items-center gap-2">
                      <span className="material-symbols-outlined text-[14px]">check_circle</span>
                      {t('auth.passwordMixedCase')}
                    </li>
                    <li className="flex items-center gap-2">
                      <span className="material-symbols-outlined text-[14px]">check_circle</span>
                      {t('auth.passwordNumbers')}
                    </li>
                  </ul>
                </div>

                {/* New Password Input */}
                <label className="flex flex-col w-full group">
                  <p className="text-gray-300 text-sm font-medium leading-normal pb-2 ml-1">
                    {t('auth.newPassword')}
                  </p>
                  <div className="flex w-full items-stretch rounded-lg relative">
                    <div className="absolute left-0 top-0 h-12 w-12 flex items-center justify-center text-gray-500 z-10 pointer-events-none">
                      <span className="material-symbols-outlined text-[20px]">lock</span>
                    </div>
                    <input
                      type={showNewPassword ? 'text' : 'password'}
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      className="flex w-full min-w-0 flex-1 resize-none overflow-hidden rounded-lg text-white placeholder:text-gray-600 focus:outline-0 focus:ring-2 focus:ring-primary/50 border border-border-dark bg-input-bg focus:border-primary h-12 pl-11 pr-12 text-base font-normal leading-normal transition-all duration-200"
                      placeholder={t('auth.newPasswordPlaceholder')}
                      required
                      minLength={10}
                    />
                    <button
                      type="button"
                      onClick={() => setShowNewPassword(!showNewPassword)}
                      className="absolute right-0 top-0 h-12 w-12 flex items-center justify-center text-gray-500 hover:text-white cursor-pointer transition-colors rounded-r-lg focus:outline-none"
                    >
                      <span className="material-symbols-outlined text-[20px]">
                        {showNewPassword ? 'visibility_off' : 'visibility'}
                      </span>
                    </button>
                  </div>
                </label>

                {/* Confirm Password Input */}
                <label className="flex flex-col w-full group">
                  <p className="text-gray-300 text-sm font-medium leading-normal pb-2 ml-1">
                    {t('auth.confirmPassword')}
                  </p>
                  <div className="flex w-full items-stretch rounded-lg relative">
                    <div className="absolute left-0 top-0 h-12 w-12 flex items-center justify-center text-gray-500 z-10 pointer-events-none">
                      <span className="material-symbols-outlined text-[20px]">lock_reset</span>
                    </div>
                    <input
                      type={showNewPassword ? 'text' : 'password'}
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      className="flex w-full min-w-0 flex-1 resize-none overflow-hidden rounded-lg text-white placeholder:text-gray-600 focus:outline-0 focus:ring-2 focus:ring-primary/50 border border-border-dark bg-input-bg focus:border-primary h-12 pl-11 pr-12 text-base font-normal leading-normal transition-all duration-200"
                      placeholder={t('auth.confirmPasswordPlaceholder')}
                      required
                      minLength={10}
                    />
                  </div>
                </label>

                {/* Error Message */}
                {(error || passwordError) && (
                  <div className="flex items-center gap-2 text-red-400 text-sm bg-red-500/10 p-3 rounded-lg border border-red-500/20">
                    <span className="material-symbols-outlined text-[18px]">error</span>
                    <span>{passwordError || error}</span>
                  </div>
                )}

                {/* Submit Button */}
                <button
                  type="submit"
                  disabled={loading || !newPassword || !confirmPassword}
                  className="flex w-full cursor-pointer items-center justify-center overflow-hidden rounded-lg h-12 px-5 bg-primary hover:bg-blue-600 active:bg-blue-700 text-white text-base font-bold leading-normal tracking-[0.015em] transition-all duration-200 shadow-[0_4px_14px_0_rgba(43,108,238,0.39)] hover:shadow-[0_6px_20px_rgba(43,108,238,0.23)] disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {loading ? (
                    <span className="material-symbols-outlined animate-spin text-[18px]">
                      progress_activity
                    </span>
                  ) : (
                    <>
                      <span className="mr-2">{t('auth.setPassword')}</span>
                      <span className="material-symbols-outlined text-[18px]">check</span>
                    </>
                  )}
                </button>
              </form>
            ) : showForgotPassword ? (
              /* Forgot Password Flow */
              <div className="flex flex-col gap-5">
                {resetPasswordState === 'success' ? (
                  /* Success State */
                  <div className="flex flex-col items-center gap-4 py-4">
                    <div className="size-16 bg-emerald-500/10 rounded-full flex items-center justify-center border border-emerald-500/20">
                      <span className="material-symbols-outlined text-emerald-500 text-[32px]">check_circle</span>
                    </div>
                    <div className="text-center">
                      <h3 className="text-white font-semibold text-lg mb-1">{t('auth.resetSuccess')}</h3>
                      <p className="text-gray-400 text-sm">{t('auth.resetSuccessMessage')}</p>
                    </div>
                    <button
                      onClick={handleBackToLogin}
                      className="flex w-full cursor-pointer items-center justify-center overflow-hidden rounded-lg h-12 px-5 bg-primary hover:bg-blue-600 active:bg-blue-700 text-white text-base font-bold leading-normal tracking-[0.015em] transition-all duration-200 shadow-[0_4px_14px_0_rgba(43,108,238,0.39)] hover:shadow-[0_6px_20px_rgba(43,108,238,0.23)]"
                    >
                      <span className="mr-2">{t('auth.backToLogin')}</span>
                      <span className="material-symbols-outlined text-[18px]">arrow_forward</span>
                    </button>
                  </div>
                ) : resetPasswordState === 'codeSent' ? (
                  /* Enter Code and New Password */
                  <form onSubmit={handleResetPasswordSubmit} className="flex flex-col gap-5">
                    {/* Info Message */}
                    <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-3">
                      <p className="text-blue-400 text-sm">{t('auth.codeSentMessage')}</p>
                    </div>

                    {/* Verification Code Input */}
                    <label className="flex flex-col w-full group">
                      <p className="text-gray-300 text-sm font-medium leading-normal pb-2 ml-1">
                        {t('auth.verificationCode')}
                      </p>
                      <div className="flex w-full items-stretch rounded-lg relative">
                        <div className="absolute left-0 top-0 h-12 w-12 flex items-center justify-center text-gray-500 z-10 pointer-events-none">
                          <span className="material-symbols-outlined text-[20px]">pin</span>
                        </div>
                        <input
                          type="text"
                          value={resetCode}
                          onChange={(e) => setResetCode(e.target.value)}
                          className="flex w-full min-w-0 flex-1 resize-none overflow-hidden rounded-lg text-white placeholder:text-gray-600 focus:outline-0 focus:ring-2 focus:ring-primary/50 border border-border-dark bg-input-bg focus:border-primary h-12 pl-11 pr-4 text-base font-normal leading-normal transition-all duration-200"
                          placeholder={t('auth.verificationCodePlaceholder')}
                          required
                        />
                      </div>
                    </label>

                    {/* Password Requirements */}
                    <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-3">
                      <p className="text-blue-400 text-xs font-medium mb-2">{t('auth.passwordRequirements')}</p>
                      <ul className="text-gray-400 text-xs space-y-1">
                        <li className="flex items-center gap-2">
                          <span className="material-symbols-outlined text-[14px]">check_circle</span>
                          {t('auth.passwordMin10')}
                        </li>
                        <li className="flex items-center gap-2">
                          <span className="material-symbols-outlined text-[14px]">check_circle</span>
                          {t('auth.passwordMixedCase')}
                        </li>
                        <li className="flex items-center gap-2">
                          <span className="material-symbols-outlined text-[14px]">check_circle</span>
                          {t('auth.passwordNumbers')}
                        </li>
                      </ul>
                    </div>

                    {/* New Password Input */}
                    <label className="flex flex-col w-full group">
                      <p className="text-gray-300 text-sm font-medium leading-normal pb-2 ml-1">
                        {t('auth.newPassword')}
                      </p>
                      <div className="flex w-full items-stretch rounded-lg relative">
                        <div className="absolute left-0 top-0 h-12 w-12 flex items-center justify-center text-gray-500 z-10 pointer-events-none">
                          <span className="material-symbols-outlined text-[20px]">lock</span>
                        </div>
                        <input
                          type={showNewPassword ? 'text' : 'password'}
                          value={resetNewPassword}
                          onChange={(e) => setResetNewPassword(e.target.value)}
                          className="flex w-full min-w-0 flex-1 resize-none overflow-hidden rounded-lg text-white placeholder:text-gray-600 focus:outline-0 focus:ring-2 focus:ring-primary/50 border border-border-dark bg-input-bg focus:border-primary h-12 pl-11 pr-12 text-base font-normal leading-normal transition-all duration-200"
                          placeholder={t('auth.newPasswordPlaceholder')}
                          required
                          minLength={10}
                        />
                        <button
                          type="button"
                          onClick={() => setShowNewPassword(!showNewPassword)}
                          className="absolute right-0 top-0 h-12 w-12 flex items-center justify-center text-gray-500 hover:text-white cursor-pointer transition-colors rounded-r-lg focus:outline-none"
                        >
                          <span className="material-symbols-outlined text-[20px]">
                            {showNewPassword ? 'visibility_off' : 'visibility'}
                          </span>
                        </button>
                      </div>
                    </label>

                    {/* Confirm Password Input */}
                    <label className="flex flex-col w-full group">
                      <p className="text-gray-300 text-sm font-medium leading-normal pb-2 ml-1">
                        {t('auth.confirmPassword')}
                      </p>
                      <div className="flex w-full items-stretch rounded-lg relative">
                        <div className="absolute left-0 top-0 h-12 w-12 flex items-center justify-center text-gray-500 z-10 pointer-events-none">
                          <span className="material-symbols-outlined text-[20px]">lock_reset</span>
                        </div>
                        <input
                          type={showNewPassword ? 'text' : 'password'}
                          value={resetConfirmPassword}
                          onChange={(e) => setResetConfirmPassword(e.target.value)}
                          className="flex w-full min-w-0 flex-1 resize-none overflow-hidden rounded-lg text-white placeholder:text-gray-600 focus:outline-0 focus:ring-2 focus:ring-primary/50 border border-border-dark bg-input-bg focus:border-primary h-12 pl-11 pr-12 text-base font-normal leading-normal transition-all duration-200"
                          placeholder={t('auth.confirmPasswordPlaceholder')}
                          required
                          minLength={10}
                        />
                      </div>
                    </label>

                    {/* Error Message */}
                    {(error || passwordError) && (
                      <div className="flex items-center gap-2 text-red-400 text-sm bg-red-500/10 p-3 rounded-lg border border-red-500/20">
                        <span className="material-symbols-outlined text-[18px]">error</span>
                        <span>{passwordError || error}</span>
                      </div>
                    )}

                    {/* Submit Button */}
                    <button
                      type="submit"
                      disabled={loading || !resetCode || !resetNewPassword || !resetConfirmPassword}
                      className="flex w-full cursor-pointer items-center justify-center overflow-hidden rounded-lg h-12 px-5 bg-primary hover:bg-blue-600 active:bg-blue-700 text-white text-base font-bold leading-normal tracking-[0.015em] transition-all duration-200 shadow-[0_4px_14px_0_rgba(43,108,238,0.39)] hover:shadow-[0_6px_20px_rgba(43,108,238,0.23)] disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {loading ? (
                        <span className="material-symbols-outlined animate-spin text-[18px]">
                          progress_activity
                        </span>
                      ) : (
                        <>
                          <span className="mr-2">{t('auth.resetPassword')}</span>
                          <span className="material-symbols-outlined text-[18px]">check</span>
                        </>
                      )}
                    </button>

                    {/* Back to Login */}
                    <button
                      type="button"
                      onClick={handleBackToLogin}
                      className="text-gray-400 text-sm hover:text-primary transition-colors flex items-center justify-center gap-1"
                    >
                      <span className="material-symbols-outlined text-[16px]">arrow_back</span>
                      {t('auth.backToLogin')}
                    </button>
                  </form>
                ) : (
                  /* Enter Email to Request Code */
                  <form onSubmit={handleForgotPasswordSubmit} className="flex flex-col gap-5">
                    {/* Info Message */}
                    <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-3">
                      <p className="text-blue-400 text-sm">{t('auth.forgotPasswordMessage')}</p>
                    </div>

                    {/* Email Input */}
                    <label className="flex flex-col w-full group">
                      <p className="text-gray-300 text-sm font-medium leading-normal pb-2 ml-1">
                        {t('auth.email')}
                      </p>
                      <div className="flex w-full items-stretch rounded-lg relative">
                        <div className="absolute left-0 top-0 h-12 w-12 flex items-center justify-center text-gray-500 z-10 pointer-events-none">
                          <span className="material-symbols-outlined text-[20px]">mail</span>
                        </div>
                        <input
                          type="email"
                          value={resetEmail}
                          onChange={(e) => setResetEmail(e.target.value)}
                          className="flex w-full min-w-0 flex-1 resize-none overflow-hidden rounded-lg text-white placeholder:text-gray-600 focus:outline-0 focus:ring-2 focus:ring-primary/50 border border-border-dark bg-input-bg focus:border-primary h-12 pl-11 pr-4 text-base font-normal leading-normal transition-all duration-200"
                          placeholder={t('auth.emailPlaceholder')}
                          required
                        />
                      </div>
                    </label>

                    {/* Error Message */}
                    {error && (
                      <div className="flex items-center gap-2 text-red-400 text-sm bg-red-500/10 p-3 rounded-lg border border-red-500/20">
                        <span className="material-symbols-outlined text-[18px]">error</span>
                        <span>{error}</span>
                      </div>
                    )}

                    {/* Submit Button */}
                    <button
                      type="submit"
                      disabled={loading || !resetEmail}
                      className="flex w-full cursor-pointer items-center justify-center overflow-hidden rounded-lg h-12 px-5 bg-primary hover:bg-blue-600 active:bg-blue-700 text-white text-base font-bold leading-normal tracking-[0.015em] transition-all duration-200 shadow-[0_4px_14px_0_rgba(43,108,238,0.39)] hover:shadow-[0_6px_20px_rgba(43,108,238,0.23)] disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {loading ? (
                        <span className="material-symbols-outlined animate-spin text-[18px]">
                          progress_activity
                        </span>
                      ) : (
                        <>
                          <span className="mr-2">{t('auth.sendCode')}</span>
                          <span className="material-symbols-outlined text-[18px]">send</span>
                        </>
                      )}
                    </button>

                    {/* Back to Login */}
                    <button
                      type="button"
                      onClick={handleBackToLogin}
                      className="text-gray-400 text-sm hover:text-primary transition-colors flex items-center justify-center gap-1"
                    >
                      <span className="material-symbols-outlined text-[16px]">arrow_back</span>
                      {t('auth.backToLogin')}
                    </button>
                  </form>
                )}
              </div>
            ) : (
              /* Login Form */
              <form onSubmit={handleSubmit} className="flex flex-col gap-5">
                {/* Username Input */}
                <label className="flex flex-col w-full group">
                  <p className="text-gray-300 text-sm font-medium leading-normal pb-2 ml-1">
                    {t('auth.username')}
                  </p>
                  <div className="flex w-full items-stretch rounded-lg relative">
                    <div className="absolute left-0 top-0 h-12 w-12 flex items-center justify-center text-gray-500 z-10 pointer-events-none">
                      <span className="material-symbols-outlined text-[20px]">person</span>
                    </div>
                    <input
                      type="text"
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      className="flex w-full min-w-0 flex-1 resize-none overflow-hidden rounded-lg text-white placeholder:text-gray-600 focus:outline-0 focus:ring-2 focus:ring-primary/50 border border-border-dark bg-input-bg focus:border-primary h-12 pl-11 pr-4 text-base font-normal leading-normal transition-all duration-200"
                      placeholder={t('auth.usernamePlaceholder')}
                      required
                      autoComplete="username"
                    />
                  </div>
                </label>

                {/* Password Input */}
                <label className="flex flex-col w-full group">
                  <p className="text-gray-300 text-sm font-medium leading-normal pb-2 ml-1">
                    {t('auth.password')}
                  </p>
                  <div className="flex w-full items-stretch rounded-lg relative">
                    <div className="absolute left-0 top-0 h-12 w-12 flex items-center justify-center text-gray-500 z-10 pointer-events-none">
                      <span className="material-symbols-outlined text-[20px]">vpn_key</span>
                    </div>
                    <input
                      type={showPassword ? 'text' : 'password'}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="flex w-full min-w-0 flex-1 resize-none overflow-hidden rounded-lg text-white placeholder:text-gray-600 focus:outline-0 focus:ring-2 focus:ring-primary/50 border border-border-dark bg-input-bg focus:border-primary h-12 pl-11 pr-12 text-base font-normal leading-normal transition-all duration-200"
                      placeholder={t('auth.passwordPlaceholder')}
                      required
                      autoComplete="current-password"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(!showPassword)}
                      className="absolute right-0 top-0 h-12 w-12 flex items-center justify-center text-gray-500 hover:text-white cursor-pointer transition-colors rounded-r-lg focus:outline-none"
                    >
                      <span className="material-symbols-outlined text-[20px]">
                        {showPassword ? 'visibility_off' : 'visibility'}
                      </span>
                    </button>
                  </div>
                </label>

                {/* Error Message */}
                {error && (
                  <div className="flex items-center gap-2 text-red-400 text-sm bg-red-500/10 p-3 rounded-lg border border-red-500/20">
                    <span className="material-symbols-outlined text-[18px]">error</span>
                    <span>{error}</span>
                  </div>
                )}

                {/* Login Button */}
                <button
                  type="submit"
                  disabled={loading || !username || !password}
                  className="flex w-full cursor-pointer items-center justify-center overflow-hidden rounded-lg h-12 px-5 bg-primary hover:bg-blue-600 active:bg-blue-700 text-white text-base font-bold leading-normal tracking-[0.015em] transition-all duration-200 shadow-[0_4px_14px_0_rgba(43,108,238,0.39)] hover:shadow-[0_6px_20px_rgba(43,108,238,0.23)] disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {loading ? (
                    <span className="material-symbols-outlined animate-spin text-[18px]">
                      progress_activity
                    </span>
                  ) : (
                    <>
                      <span className="mr-2">{t('auth.login')}</span>
                      <span className="material-symbols-outlined text-[18px]">arrow_forward</span>
                    </>
                  )}
                </button>

                {/* Forgot Password Link */}
                <button
                  type="button"
                  onClick={() => setShowForgotPassword(true)}
                  className="text-gray-400 text-sm hover:text-primary transition-colors"
                >
                  {t('auth.forgotPassword')}
                </button>
              </form>
            )}
          </div>

          {/* Footer */}
          <div className="bg-input-bg/50 p-4 border-t border-border-dark flex justify-center">
            <p className="text-gray-500 text-xs font-normal leading-normal text-center">
              2024 anthropic_api_proxy. v1.0.0
            </p>
          </div>
        </div>

        {/* Help Links */}
        <div className="mt-8 flex gap-4 text-sm text-gray-500">
          <a href="#" className="hover:text-primary transition-colors flex items-center gap-1">
            <span className="material-symbols-outlined text-[16px]">help</span>
            {t('auth.needHelp')}
          </a>
          <span className="text-gray-700">|</span>
          <a href="#" className="hover:text-primary transition-colors">
            {t('auth.privacyPolicy')}
          </a>
        </div>
      </div>
    </div>
  );
}
