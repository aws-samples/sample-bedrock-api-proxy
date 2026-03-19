import { Routes, Route, Navigate } from 'react-router-dom';
import { MainLayout } from './components/Layout';
import { Login, Dashboard, ApiKeys, Pricing, ModelMapping } from './pages';
import ProviderKeys from './pages/ProviderKeys';
import RoutingConfig from './pages/RoutingConfig';
import FailoverConfig from './pages/FailoverConfig';
import { useAuth } from './hooks';

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { authenticated, authState } = useAuth();

  // Show nothing while checking auth state
  if (authState === 'idle') {
    return null;
  }

  if (!authenticated) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const { authenticated, authState } = useAuth();

  // Show nothing while checking auth state
  if (authState === 'idle') {
    return null;
  }

  if (authenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      {/* Public routes */}
      <Route
        path="/login"
        element={
          <PublicRoute>
            <Login />
          </PublicRoute>
        }
      />

      {/* Protected routes */}
      <Route
        element={
          <ProtectedRoute>
            <MainLayout />
          </ProtectedRoute>
        }
      >
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/api-keys" element={<ApiKeys />} />
        <Route path="/pricing" element={<Pricing />} />
        <Route path="/model-mapping" element={<ModelMapping />} />
        <Route path="/provider-keys" element={<ProviderKeys />} />
        <Route path="/routing" element={<RoutingConfig />} />
        <Route path="/failover" element={<FailoverConfig />} />
      </Route>

      {/* Default redirect */}
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
