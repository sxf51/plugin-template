/**
 * Page logic - every method window.CapstonePluginPage exposes, used once.
 *
 * The bridge is injected by the host, so it already exists when this file
 * runs. `ready()` resolves with the page context (user, locale, theme) and
 * `onContext()` fires again whenever the reader changes language or theme.
 *
 * Plain ES5-ish JavaScript, no build step and no imports: the page is served
 * as static files, and nothing is bundled for you.
 */
;(function () {
  var bridge = window.CapstonePluginPage

  // Text lives here, not in the HTML, because the reader can switch language
  // while the page is open. The backend only ever returns message codes.
  var TEXT = {
    en: {
      title: 'Plugin Template Console',
      subtitle: 'Every page-bridge method, once.',
      composeTitle: 'Write a note',
      composeHint: "bridge.apiPost('notes', {...})",
      titlePlaceholder: 'Title',
      bodyPlaceholder: 'Body',
      save: 'Save',
      listTitle: 'Your notes',
      listHint: "apiGet('recent') · apiPut('notes/update') · apiDelete('notes/delete')",
      refresh: 'Refresh',
      uploadTitle: 'Attachments',
      uploadHint: "upload('attachments') · download('attachments/file') · apiDelete('attachments/delete')",
      save_as: 'Save',
      noFiles: 'No files yet.',
      binaryTitle: 'Binaries',
      binaryHint: "bridge.dataUrl('badge') · bridge.download('export')",
      reloadBadge: 'Reload badge',
      export: 'Download JSON',
      streamTitle: 'Activity stream',
      streamHint: "bridge.subscribeSSE('events', handlers)",
      streamStart: 'Start',
      streamStop: 'Stop',
      toolTitle: 'Run a tool',
      toolHint: "The host's tool executor, no LLM turn",
      runStatus: 'template_status_tool',
      ping: 'Ping',
      rename: 'Rename',
      remove: 'Delete',
      empty: 'No notes yet.',
      saved: 'Note saved',
      deleted: 'Note deleted',
      uploaded: 'Uploaded',
      removed: 'File deleted',
      // Backend message codes. Anything unmapped falls back to the raw code,
      // which is still more useful to a reader than a blank failure.
      missing_text: 'Write something first',
      missing_id: 'That note has no id',
      note_not_found: 'That note is gone',
      file_too_large: 'File is larger than 1 MiB',
      missing_file: 'Pick a file first',
      empty_file: 'That file is empty',
      attachment_not_found: 'That file is gone',
      attachment_file_missing: 'The bytes for that file are missing',
      storage_unavailable: 'Storage is unavailable',
      tool_timeout: 'The tool timed out',
      tool_failed: 'The tool failed',
      tool_not_allowed: 'That tool is not part of this plugin',
      executor_unavailable: 'The tool executor is unavailable',
    },
    zh: {
      title: '模板控制台',
      subtitle: '页面桥接的每个方法，各示范一次。',
      composeTitle: '写一条笔记',
      composeHint: "bridge.apiPost('notes', {...})",
      titlePlaceholder: '标题',
      bodyPlaceholder: '正文',
      save: '保存',
      listTitle: '你的笔记',
      listHint: "apiGet('recent') · apiPut('notes/update') · apiDelete('notes/delete')",
      refresh: '刷新',
      uploadTitle: '附件',
      uploadHint: "upload('attachments') · download('attachments/file') · apiDelete('attachments/delete')",
      save_as: '下载',
      noFiles: '还没有文件。',
      binaryTitle: '二进制内容',
      binaryHint: "bridge.dataUrl('badge') · bridge.download('export')",
      reloadBadge: '重新加载徽标',
      export: '下载 JSON',
      streamTitle: '活动流',
      streamHint: "bridge.subscribeSSE('events', handlers)",
      streamStart: '开始',
      streamStop: '停止',
      toolTitle: '运行工具',
      toolHint: '走宿主的工具执行器，不消耗一次模型对话',
      runStatus: 'template_status_tool',
      ping: '探活',
      rename: '改名',
      remove: '删除',
      empty: '还没有笔记。',
      saved: '已保存',
      deleted: '已删除',
      uploaded: '已上传',
      removed: '文件已删除',
      missing_text: '先写点内容',
      missing_id: '这条笔记没有 id',
      note_not_found: '这条笔记已不存在',
      file_too_large: '文件超过 1 MiB',
      missing_file: '请先选择文件',
      empty_file: '文件是空的',
      attachment_not_found: '该文件已不存在',
      attachment_file_missing: '该文件的内容已丢失',
      storage_unavailable: '存储不可用',
      tool_timeout: '工具调用超时',
      tool_failed: '工具调用失败',
      tool_not_allowed: '该工具不属于本插件',
      executor_unavailable: '工具执行器不可用',
    },
  }

  var locale = 'en'
  var subscriptionId = null
  // The last rows each list rendered. Kept because a language change has to
  // repaint text this page built itself - data-i18n only covers the static
  // markup, and re-fetching on every toggle would be a request for nothing.
  var lastNotes = []
  var lastFiles = []

  // The bridge also has t(key, fallback), which reads context.i18n. The host
  // currently sends that empty, so a page carries its own table like this one.
  function t(key) {
    var table = TEXT[locale] || TEXT.en
    return table[key] || TEXT.en[key] || key
  }

  /** Turn a rejected bridge promise into text this reader can act on. */
  function explain(error) {
    var raw = (error && (error.message || error.code)) || String(error)
    // The backend answers {"status":"error","message":"<code>"}; the bridge
    // surfaces that message as the rejection reason.
    var code = String(raw).replace(/^Error:\s*/, '').trim()
    return t(code)
  }

  function byId(id) {
    return document.getElementById(id)
  }

  /** Re-render every translatable string. Called on load and on each change. */
  function paint() {
    var nodes = document.querySelectorAll('[data-i18n]')
    for (var index = 0; index < nodes.length; index += 1) {
      nodes[index].textContent = t(nodes[index].getAttribute('data-i18n'))
    }
    var placeholders = document.querySelectorAll('[data-i18n-placeholder]')
    for (var pIndex = 0; pIndex < placeholders.length; pIndex += 1) {
      placeholders[pIndex].placeholder = t(placeholders[pIndex].getAttribute('data-i18n-placeholder'))
    }
    byId('locale').textContent = locale
    // Anything this page rendered from data carries no data-i18n attribute, so
    // the loop above cannot reach it. Redraw those lists from what they last
    // showed, or a language switch leaves half the page in the old language.
    renderNotes(lastNotes)
    renderFiles(lastFiles)
  }

  /** Fill a list, or show its empty message when there is nothing to show. */
  function fillList(id, rows, emptyKey, renderRow) {
    var list = byId(id)
    if (!list) return
    list.innerHTML = ''
    if (!rows.length) {
      var empty = document.createElement('li')
      empty.className = 'muted'
      empty.textContent = t(emptyKey)
      list.appendChild(empty)
      return
    }
    rows.forEach(function (row) {
      list.appendChild(renderRow(row))
    })
  }

  // --- data ---------------------------------------------------------------

  function loadNotes() {
    // apiGet(endpoint, params) -> the host adds auth and the plugin prefix.
    return bridge.apiGet('recent', { limit: 20 }).then(function (rows) {
      renderNotes(rows || [])
    }, fail)
  }

  function renderNotes(rows) {
    lastNotes = rows
    fillList('notes', rows, 'empty', renderNote)
  }

  function renderNote(note) {
    var item = document.createElement('li')
    var label = document.createElement('span')
    label.textContent = note.title + '  ·  ' + note.tag
    item.appendChild(label)

    var rename = document.createElement('button')
    rename.className = 'link'
    rename.textContent = t('rename')
    rename.onclick = function () {
      // apiPut: the same JSON body shape as apiPost.
      bridge
        .apiPut('notes/update', { id: note.id, title: note.title + ' *' })
        .then(loadNotes, fail)
    }
    item.appendChild(rename)

    var remove = document.createElement('button')
    remove.className = 'link danger'
    remove.textContent = t('remove')
    remove.onclick = function () {
      // apiDelete takes query params, not a body.
      bridge.apiDelete('notes/delete', { id: note.id }).then(function () {
        bridge.notify(t('deleted'), 'success')
        return loadNotes()
      }, fail)
    }
    item.appendChild(remove)
    return item
  }

  function loadAttachments() {
    return bridge.apiGet('attachments/list', { limit: 20 }).then(function (rows) {
      renderFiles(rows || [])
    }, fail)
  }

  function renderFiles(rows) {
    lastFiles = rows
    fillList('attachments', rows, 'noFiles', renderAttachment)
  }

  function renderAttachment(file) {
    var item = document.createElement('li')
    var label = document.createElement('span')
    // The original name and format survive the round trip; only the name on
    // disk is generated by the backend.
    label.textContent = file.name + '  ·  ' + Math.ceil(file.bytes / 1024) + ' KiB'
    label.title = file.content_type
    item.appendChild(label)

    var save = document.createElement('button')
    save.className = 'link'
    save.textContent = t('save_as')
    save.onclick = function () {
      // The host performs the save; the sandbox blocks a download the page
      // starts itself. Pass the original name so the save dialog shows it.
      bridge.download('attachments/file', { id: file.id }, file.name).catch(fail)
    }
    item.appendChild(save)

    var remove = document.createElement('button')
    remove.className = 'link danger'
    remove.textContent = t('remove')
    remove.onclick = function () {
      bridge.apiDelete('attachments/delete', { id: file.id }).then(function () {
        bridge.notify(t('removed'), 'success')
        loadStats()
        return loadAttachments()
      }, fail)
    }
    item.appendChild(remove)
    return item
  }

  function loadBadge() {
    // The page cannot fetch a binary itself, so the host does and returns a
    // data: URL ready for an <img> src.
    bridge.dataUrl('badge').then(function (url) {
      byId('badge').src = url
    }, fail)
  }

  function loadStats() {
    bridge.apiGet('stats').then(function (body) {
      byId('backend').textContent = 'storage: ' + body.backend
    }, fail)
  }

  function fail(error) {
    // notify() raises a toast in the dashboard shell, outside the iframe.
    bridge.notify(explain(error), 'error')
  }

  // --- wiring -------------------------------------------------------------

  byId('save').onclick = function () {
    bridge
      .apiPost('notes', {
        title: byId('note-title').value,
        body: byId('note-body').value,
        tag: byId('note-tag').value,
      })
      .then(function () {
        byId('note-title').value = ''
        byId('note-body').value = ''
        bridge.notify(t('saved'), 'success')
        loadStats()
        loadBadge()
        return loadNotes()
      }, fail)
  }

  byId('refresh').onclick = loadNotes
  byId('reload-badge').onclick = loadBadge

  byId('export').onclick = function () {
    // The sandbox blocks a download the page starts, so the host saves it.
    bridge.download('export', {}, 'template-notes.json').catch(fail)
  }

  byId('file').onchange = function (event) {
    var file = event.target.files && event.target.files[0]
    if (!file) return
    // Check the size here as a courtesy to the reader, never as the real
    // limit: this is page code, so the backend enforces its own cap.
    if (file.size > 1024 * 1024) {
      bridge.notify(t('file_too_large'), 'error')
      event.target.value = ''
      return
    }
    // upload() reads the bytes in this frame and sends the buffer; the handler
    // receives it as the form field `file`. Handing the host the File object
    // instead fails with net::ERR_ACCESS_DENIED - see the frontend guide.
    bridge.upload('attachments', file).then(function (body) {
      byId('upload-result').textContent = t('uploaded') + ': ' + body.attachment.name
      byId('file').value = ''
      bridge.notify(t('uploaded'), 'success')
      loadStats()
      return loadAttachments()
    }, fail)
  }

  byId('stream-start').onclick = function () {
    if (subscriptionId !== null) return
    byId('stream').textContent = ''
    bridge
      .subscribeSSE('events', {
        // The handler receives {raw, parsed}: `raw` is the text after
        // "data: ", `parsed` is that text as JSON or null when it is not JSON.
        onMessage: function (event) {
          var view = byId('stream')
          view.textContent += event.raw + '\n'
          view.scrollTop = view.scrollHeight
        },
        onError: fail,
        // Named onClose, not onEnd; it fires when the server ends the stream.
        onClose: function () {
          subscriptionId = null
        },
      })
      .then(function (id) {
        subscriptionId = id
      }, fail)
  }

  byId('stream-stop').onclick = function () {
    if (subscriptionId === null) return
    bridge.unsubscribeSSE(subscriptionId)
    subscriptionId = null
  }

  byId('run-status').onclick = function () {
    bridge.apiPost('run-tool', { tool: 'template_status_tool' }).then(function (body) {
      byId('tool-out').textContent = JSON.stringify(body.result, null, 2)
    }, fail)
  }

  byId('ping').onclick = function () {
    // The endpoint published by the @plugin_web_api decorator.
    bridge.apiGet('ping').then(function (body) {
      bridge.notify('pong · ' + body.user, 'info')
    }, fail)
  }

  // --- context ------------------------------------------------------------

  // ready() resolves with the page context; the host pushes it on frame load,
  // and this asks again in case this script ran after that push.
  bridge.ready().then(function (context) {
    // The context carries pluginName, pageName, pageTitle, locale, isDark and
    // i18n - deliberately not the user's identity. Ask the backend for that:
    // it knows who is calling from the host's own authentication.
    byId('who').textContent = context.pageTitle || context.pageName || '—'
    bridge.apiGet('ping').then(function (body) {
      byId('who').textContent = body.user
    }, fail)

    var select = byId('note-tag')
    ;['general', 'idea', 'todo'].forEach(function (tag) {
      var option = document.createElement('option')
      option.value = tag
      option.textContent = tag
      select.appendChild(option)
    })

    loadStats()
    loadBadge()
    loadNotes()
    loadAttachments()
  }, fail)

  // Fires on load and again on every theme or language change. The host also
  // sets data-theme on <html> itself, so CSS alone handles the colours; only
  // the text needs repainting here.
  bridge.onContext(function (context) {
    locale = context.locale === 'zh' ? 'zh' : 'en'
    paint()
  })

  paint()
})()
