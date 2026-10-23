// @vitest-environment jsdom
import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ConversationInteraction from './ConversationInteraction';

// Keep the real CopilotKit context, core and rendering hooks. Only the platform's
// HTTP-backed business host is replaced to isolate the renderer integration.
vi.mock('./InteractionHost',()=>({default:({instanceId,surface}:{instanceId:string;surface:string})=><div>registered:{instanceId}:{surface}</div>}));
afterEach(()=>{cleanup();vi.unstubAllEnvs();vi.unstubAllGlobals();vi.restoreAllMocks();});
it('uses the installed CopilotKit v2 hooks to mount the platform interaction',async()=>{
  render(<ConversationInteraction instanceId="renderer-proof"/>);
  expect(await screen.findByText('registered:renderer-proof:conversation')).toBeTruthy();
});

it('renders in production without a runtime URL, cloud key or network request',async()=>{
  vi.stubEnv('NODE_ENV','production');
  const fetch=vi.fn(()=>Promise.reject(new Error('Rendering must not make transport requests')));
  vi.stubGlobal('fetch',fetch);
  const error=vi.spyOn(console,'error');
  const warn=vi.spyOn(console,'warn');
  render(<ConversationInteraction instanceId="production-proof"/>);
  expect(await screen.findByText('registered:production-proof:conversation')).toBeTruthy();
  expect(fetch).not.toHaveBeenCalled();
  expect(error).not.toHaveBeenCalled();
  expect(warn).not.toHaveBeenCalled();
});

it('keeps simultaneous component references distinct and can remount history',async()=>{
  const view=render(<><ConversationInteraction instanceId="instance-a"/><ConversationInteraction instanceId="instance-b"/></>);
  expect(await screen.findByText('registered:instance-a:conversation')).toBeTruthy();
  expect(await screen.findByText('registered:instance-b:conversation')).toBeTruthy();
  view.unmount();
  render(<ConversationInteraction instanceId="instance-a"/>);
  expect(await screen.findByText('registered:instance-a:conversation')).toBeTruthy();
  expect(screen.queryByText('registered:instance-b:conversation')).toBeNull();
});
