import { Outlet, Navigate } from 'react-router-dom';
import { isAuthenticated } from '../../services/api';
import Sidebar from './Sidebar';
import Header from './Header';

export default function MainLayout() {
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace />;
  }

  return (
    <div className="flex h-screen w-full bg-background-dark text-white overflow-hidden">
      <Sidebar />
      <main className="flex-1 flex flex-col h-full overflow-hidden">
        <Header />
        <div className="flex-1 overflow-y-auto p-6 md:p-10">
          <div className="max-w-7xl mx-auto">
            <Outlet />
          </div>
        </div>
      </main>
    </div>
  );
}
