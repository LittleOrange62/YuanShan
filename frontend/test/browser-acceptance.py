"""Run through browser-harness in a fresh dedicated Chrome profile.
BU_NAME=astock-a1 BU_CDP_URL=http://127.0.0.1:9227 browser-harness < frontend/test/browser-acceptance.py
All API requests are intercepted in this test tab; no private data/model calls.
"""
import json
import time
import os
from pathlib import Path

out = Path('.local/alignment-a1-20260907').resolve()
out.mkdir(parents=True, exist_ok=True)
checks = []

def check(name, value):
    checks.append({'name': name, 'passed': bool(value)})
    if not value:
        print('FAILED:', name)

def eventually(expression, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if js(expression):
            return True
        wait(.1)
    return False

def ready():
    # 对话面板住在「AI 探索」页的 AI 对话 tab；先回首页等页面骨架，再切过去。
    wait_for_element('main', timeout=10)
    wait(0.25)

def chat_ready():
    goto_url('http://127.0.0.1:5918/settings')
    wait_for_element('[data-home-agent]', timeout=10)
    wait(0.25)

def click_label(label):
    selector = f'button[aria-label="{label}"]'
    js(f'document.querySelector({json.dumps(selector)})?.focus(); document.querySelector({json.dumps(selector)})?.click()')
    wait(0.1)

assert os.environ.get('BU_CDP_URL') == 'http://127.0.0.1:9227'
assert os.environ.get('BU_NAME') == 'astock-a1'
new_tab('about:blank')
cdp('Page.bringToFront')
cdp('Network.enable')
cdp('Network.setBlockedURLs', urls=['*/api/*'])
fixture = cdp('Page.addScriptToEvaluateOnNewDocument', source='''
window.__a1Calls = [];
window.__a1Health = {installed:true, subscription_ready:true, models_error:'', models:[], default_model:''};
window.__a1Delay = 0;
const realFetch = window.fetch.bind(window);
window.fetch = async (input, init) => {
 const url = input instanceof Request ? input.url : String(input);
 const method = String(init?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
 if (!new URL(url, location.href).pathname.startsWith('/api/')) return realFetch(input, init);
 window.__a1Calls.push({url, method});
 const total = JSON.parse(sessionStorage.getItem('astock-a1-test-calls') || '[]');
 total.push({url, method});
 sessionStorage.setItem('astock-a1-test-calls', JSON.stringify(total));
 if (url.endsWith('/review-agent/status')) {
   await new Promise(resolve => setTimeout(resolve, window.__a1Delay));
   return new Response(JSON.stringify(window.__a1Health), {headers:{'Content-Type':'application/json'}});
 }
 if (url.endsWith('/review-agent/access')) return new Response(JSON.stringify({status:'idle'}));
 return new Response(JSON.stringify({detail:'A1 合成空数据测试，未访问业务后端'}), {status:503});
};
''')['identifier']
try:
    goto_url('http://127.0.0.1:5918/')
    js("sessionStorage.removeItem('astock-a1-test-calls'); localStorage.removeItem('astock-agent-connection');localStorage.removeItem('astock-agent-sources');localStorage.removeItem('astock-agent-active');localStorage.removeItem('astock-agent-failover'); localStorage.removeItem('astock-workspace-agent-v1'); localStorage.removeItem('vr-theme'); localStorage.removeItem('va-sidebar')")
    cdp('Emulation.setDeviceMetricsOverride', width=1440, height=1000, deviceScaleFactor=1, mobile=False)
    goto_url('http://127.0.0.1:5918/')
    ready()
    # 接入向导弹窗已删除（用户裁定「AI 的配置不需要弹窗」）：首跑必须直接落在工作台上，
    # 配置改到「AI 探索」页就地完成，且这些步骤一次模型请求都不许发。
    # 首跑落在新首页（原汇总看板改名），且不弹任何向导；功能格已搬到「系统配置」。
    check('first run lands on the renamed home with no modal', js("!document.querySelector('dialog[open]') && Array.from(document.querySelectorAll('h1')).some(e=>e.textContent.trim()==='首页')"))
    check('first run no model/status request', js("!window.__a1Calls.some(c=>c.method==='POST' || c.url.includes('/review-agent/'))"))
    chat_ready()
    check('AI 探索 opens with two tabs, chat first', js("document.querySelectorAll('[role=tab]').length === 2 && document.querySelectorAll('[role=tab]')[0].getAttribute('aria-selected') === 'true' && !!document.querySelector('[data-home-agent]')"))
    capture_screenshot(str(out/'ai-chat-tab.png'))
    js("Array.from(document.querySelectorAll('[role=tab]')).find(e=>e.textContent.includes('接入设置')).click()")
    wait(0.2)
    check('access tab offers the same providers without a popup', js("document.body.innerText.includes('使用订阅') && document.body.innerText.includes('AI 来源') && !document.querySelector('[data-home-agent]')"))
    check('configuring access still sends no model request', js("!window.__a1Calls.some(c=>c.method==='POST')"))
    capture_screenshot(str(out/'first-connect.png'))
    goto_url('http://127.0.0.1:5918/system')
    wait(.35)
    check('feature grid now lives under 系统配置', js("!!document.querySelector('#home-features') && document.querySelectorAll('[data-feature-category]').length === 6 && document.querySelectorAll('#home-features a').length === 25"))
    check('Agent defaults OFF', js("document.querySelector('[role=switch]').getAttribute('aria-checked') === 'false'"))
    check('wide no document overflow', js('document.documentElement.scrollWidth === innerWidth'))
    capture_screenshot(str(out/'home-dark.png'))
    click_label('切换为浅色')
    check('light theme applied', js("document.documentElement.classList.contains('light')"))
    capture_screenshot(str(out/'home-light.png'))
    goto_url('http://127.0.0.1:5918/')
    ready()
    press_key('Escape')
    check('theme persists after reload', js("document.documentElement.classList.contains('light')"))
    check('footer persists and external link safe', js("(()=>{const a=document.querySelector('aside a[href=\"https://www.douyin.com/\"]');return !!a && a.rel.includes('noopener') && a.target==='_blank' && a.getBoundingClientRect().bottom<=innerHeight})()"))
    chat_ready()   # 标题在对话面板里，面板现在住在「AI 探索」
    js("document.querySelector('[role=switch]').click()")
    wait(.1)
    check('mode changes chat heading', js("document.body.innerText.includes('今天，想复盘什么？')"))
    goto_url('http://127.0.0.1:5918/')
    ready()
    press_key('Escape')
    check('explicit Agent choice persists', js("document.querySelector('[role=switch]').getAttribute('aria-checked') === 'true'"))
    chat_ready()
    js("document.querySelector('[data-home-agent] .grid button').click()")
    wait(.1)
    check('suggestion fills composer without sending', js("document.querySelector('textarea').value.length>0 && !window.__a1Calls.some(c=>c.method==='POST')"))
    js("document.querySelector('textarea').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,isComposing:true}))")
    check('IME Enter does not submit', js("document.querySelector('textarea').value.length>0 && !document.querySelector('[role=alert]')"))
    press_key('Enter')
    wait(.1)
    check('preview send does not invoke model', js("document.querySelector('[role=alert]')?.textContent.includes('没有发送') && !window.__a1Calls.some(c=>c.method==='POST')"))
    js("Array.from(document.querySelectorAll('button')).find(e=>e.textContent==='放回草稿').click()")
    wait(.1)
    check('unsent draft recoverable', js("document.querySelector('textarea').value.length>0"))
    click_label('收起侧栏')
    check('collapsed links have accessible names', js("Array.from(document.querySelectorAll('aside nav a')).every(a=>a.getAttribute('aria-label'))"))
    capture_screenshot(str(out/'collapsed.png'))
    click_label('展开侧栏')

    # Synthetic status matrix, never use a real credential.
    js("localStorage.setItem('astock-agent-connection',JSON.stringify({provider:'codex-private',model:'synthetic-model',baseURL:'',apiKey:''})); window.__a1Delay=1500; window.dispatchEvent(new Event('astock-connection-changed'))")
    wait(.1)
    check('checking state shown', eventually("document.body.innerText.includes('正在检测 AI 接入')"))
    wait(1.5)
    check('local login not presented as successful connection', eventually("document.body.innerText.includes('连接待实测')"))
    for name, health, expected in [
        ('missing engine', {'installed':False,'subscription_ready':False,'models_error':''}, '产品引擎未就绪'),
        ('logged out', {'installed':True,'subscription_ready':False,'models_error':''}, '未登录或登录失效'),
        ('catalog failure', {'installed':True,'subscription_ready':True,'models_error':'synthetic failure'}, '无法确认 AI 状态'),
    ]:
        js(f"window.__a1Delay=0; window.__a1Health={{models:[],default_model:'',...{json.dumps(health)}}}; window.dispatchEvent(new Event('astock-connection-changed'))")
        wait(.2)
        check(name, eventually(f'document.body.innerText.includes({json.dumps(expected)})'))
    js("localStorage.removeItem('astock-agent-connection');localStorage.removeItem('astock-agent-sources');localStorage.removeItem('astock-agent-active');localStorage.removeItem('astock-agent-failover'); window.dispatchEvent(new Event('astock-connection-changed'))")
    wait(.1)
    paths = ['/','/agent/review','/first-board','/heat','/stock-data','/agent/deepdive','/intel','/my-stocks','/grouped','/journal','/backtest','/settings']
    titles = ['首页','短线复盘看板','首板分析','近5天热度','每日盯盘','开盘核验','个股数据','个股深挖','资讯雷达','重点关注（持仓）','分组自选','交易日志','策略回测','AI 探索']
    for route, title in zip(paths, titles):
        selector = f'aside nav a[href="{route}"]'
        js(f'document.querySelector({json.dumps(selector)}).click()')
        wait(.35)
        check('old route mounts '+route, eventually(f"location.pathname==={json.dumps(route)} && Array.from(document.querySelectorAll('main h1')).some(e=>e.textContent.trim()==={json.dumps(title)}) && !document.body.innerText.includes('Unexpected Application Error')"))
    # 「AI 探索」页本身就内嵌对话，不再挂浮层按钮；浮层检查换到普通页面做。
    js("document.querySelector('aside nav a[href=\"/journal\"]').click()")
    wait(.35)
    js("Array.from(document.querySelectorAll('header button')).find(e=>e.textContent.includes('问 ' ) || e.textContent.includes('问模型')).click()")
    wait(.1)
    check('page chat shares composer and context title', js("!!document.querySelector('dialog textarea') && document.querySelector('dialog').innerText.includes('交易日志')"))
    press_key('Escape')
    js("document.querySelector('aside nav a[href=\"/\"]').click()")
    ready()
    press_key('Escape')
    cdp('Emulation.setDeviceMetricsOverride',width=390,height=844,deviceScaleFactor=1,mobile=True)
    wait(.2)
    check('narrow no document overflow', js('document.documentElement.scrollWidth === innerWidth'))
    capture_screenshot(str(out/'home-mobile-light.png'))
    click_label('打开导航')
    check('mobile nav modal open', js("!!document.querySelector('dialog[open] nav')"))
    check('mobile footer visible', js("document.querySelector('dialog a[href=\"https://www.douyin.com/\"]').getBoundingClientRect().bottom < innerHeight"))
    capture_screenshot(str(out/'mobile-navigation.png'))
    press_key('Escape')
    wait(.1)
    check('mobile Escape returns focus', js("document.activeElement.getAttribute('aria-label')==='打开导航'"))
    click_label('切换为深色')
    capture_screenshot(str(out/'home-mobile-dark.png'))
    check('UI sequence made zero POST calls', js("!JSON.parse(sessionStorage.getItem('astock-a1-test-calls') || '[]').some(c=>c.method==='POST')"))
finally:
    js("sessionStorage.removeItem('astock-a1-test-calls'); localStorage.removeItem('astock-agent-connection');localStorage.removeItem('astock-agent-sources');localStorage.removeItem('astock-agent-active');localStorage.removeItem('astock-agent-failover'); localStorage.setItem('astock-workspace-agent-v1','off'); localStorage.setItem('vr-theme','dark')")
    cdp('Emulation.setDeviceMetricsOverride',width=1440,height=1000,deviceScaleFactor=1,mobile=False)
    goto_url('about:blank')
    cdp('Page.removeScriptToEvaluateOnNewDocument',identifier=fixture)
    cdp('Network.setBlockedURLs', urls=[])
    (out/'browser-checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
print(json.dumps({'passed':sum(c['passed'] for c in checks),'total':len(checks),'failed':[c for c in checks if not c['passed']]},ensure_ascii=False))

assert all(c['passed'] for c in checks), 'Browser acceptance checks failed'
