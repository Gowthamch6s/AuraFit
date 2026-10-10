
import { describe, it, expect, vi, afterEach } from 'vitest';
import { demo, loadDashboard, loadTrend, ApiError } from '../src/data';

afterEach(() => vi.unstubAllGlobals());

describe('Phase 5 API adapter', () => {
  it('demo discloses synthetic observations', () => {
    expect(demo.demo).toBe(true);
    expect(demo.forecast.predicted_recovery_score).toBeNull();
    expect(demo.targets.calorie_intake_target_kcal).toBeNull();
  });

  it('fetches exact Phase 4 schemas with scoped Authorization', async () => {
    const mock = vi.fn(async (url: string, _options: RequestInit) => ({
      ok: true,
      json: async () =>
        url.includes('metrics')
          ? demo.assessment
          : url.includes('targets')
            ? demo.targets
            : demo.forecast
    }));

    vi.stubGlobal('fetch', mock);

    const result = await loadDashboard('af_test');

    expect(result.assessment.assessment.recovery_score).toBe(86);
    expect(result.demo).toBe(false);
    expect(mock).toHaveBeenCalledTimes(3);

    expect(mock.mock.calls[0][1].headers).toMatchObject({
      Authorization: 'Bearer af_test'
    });
  });

  it('provides meaningful structured error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 401,
        json: async () => ({
          error: { message: 'Invalid bearer token' }
        })
      }))
    );

    await expect(loadDashboard('wrong')).rejects.toThrow(
      'Invalid bearer token'
    );
  });

  it('omits insufficient-data dates in history', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 409,
        json: async () => ({
          error: { message: 'Insufficient data' }
        })
      }))
    );

    expect(await loadTrend('token', 3)).toEqual([]);
  });

  it('API error retains status', () => {
    expect(new ApiError(503, 'down').status).toBe(503);
  });
});
