#!/usr/bin/env node

function getTonieContentId(item) {
  return String(item?.content_id || item?.contentId || item?.id || '').trim();
}

function dedupeTonieContentItems(items = []) {
  const byId = new Map();
  for (const item of items) {
    const id = getTonieContentId(item);
    if (!id) continue;
    byId.set(id, item);
  }
  return Array.from(byId.values());
}

function removeTonieContentItemsById(items = [], ids = []) {
  const set = new Set((ids || []).map((x) => String(x || '').trim()).filter(Boolean));
  return (items || []).filter((item) => !set.has(getTonieContentId(item)));
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function run() {
  const initial = [
    { content_id: 'a1', name: 'Party Freeze Dance Song' },
    { content_id: 'a2', name: 'Party Freeze Dance Song' },
    { content_id: 'a2', name: 'Party Freeze Dance Song' }, // duplicate id
    { content_id: 'b1', name: 'Another Song' },
  ];

  const deduped = dedupeTonieContentItems(initial);
  assert(deduped.length === 3, 'dedupe should collapse repeated content_id rows');

  const afterDeleteOne = removeTonieContentItemsById(deduped, ['a1']);
  assert(afterDeleteOne.length === 2, 'delete by id should remove exactly one duplicate-title item');
  assert(afterDeleteOne.every((x) => x.content_id !== 'a1'), 'a1 should be removed');

  const afterDeleteBoth = removeTonieContentItemsById(afterDeleteOne, ['a2']);
  assert(afterDeleteBoth.length === 1, 'delete by id should remove second duplicate-title item independently');
  assert(afterDeleteBoth[0].content_id === 'b1', 'remaining item should be unrelated track');

  console.log('PASS tonies_delete_model_check');
}

try {
  run();
} catch (err) {
  console.error('FAIL tonies_delete_model_check:', err.message || err);
  process.exit(1);
}
