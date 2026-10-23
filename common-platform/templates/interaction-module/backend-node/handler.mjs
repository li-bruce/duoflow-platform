export async function handle(input, actor) {
  // actor comes only from the authenticated platform envelope. Add business
  // object ownership and role checks here, never trust an input.actor field.
  if (!actor.id) throw new Error('Missing actor');
  return {summary:'已完成远程回显。',data:{text:input.message}};
}
