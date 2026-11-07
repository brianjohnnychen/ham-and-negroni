-- Ham & Negroni: database schema, row-level security, realtime and storage.
-- Idempotent: safe to run again on an existing project.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------- profiles
create table if not exists public.profiles (
  id          uuid primary key references auth.users (id) on delete cascade,
  display_name text not null check (char_length(display_name) between 1 and 40),
  drink       text not null default '' check (char_length(drink) <= 60),
  bio         text not null default '' check (char_length(bio) <= 280),
  avatar_url  text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------- verdicts
-- Append-only log. 'round' = another round (like), 'night' = call it a night (dislike).
create table if not exists public.verdicts (
  id          bigint generated always as identity primary key,
  actor       uuid not null references public.profiles (id) on delete cascade,
  target      uuid not null references public.profiles (id) on delete cascade,
  kind        text not null check (kind in ('round', 'night')),
  created_at  timestamptz not null default now(),
  constraint verdicts_not_self check (actor <> target)
);
create index if not exists verdicts_created_idx on public.verdicts (created_at desc);
create index if not exists verdicts_pair_idx on public.verdicts (actor, target, created_at desc);

-- ---------------------------------------------------------------- messages
create table if not exists public.messages (
  id          bigint generated always as identity primary key,
  sender      uuid not null references public.profiles (id) on delete cascade,
  recipient   uuid not null references public.profiles (id) on delete cascade,
  body        text not null check (char_length(body) between 1 and 2000),
  created_at  timestamptz not null default now(),
  constraint messages_not_self check (sender <> recipient)
);
create index if not exists messages_sender_idx on public.messages (sender, created_at desc);
create index if not exists messages_recipient_idx on public.messages (recipient, created_at desc);

-- ---------------------------------------------------------------- current verdict per pair
create or replace view public.current_verdicts
with (security_invoker = true) as
select distinct on (actor, target) actor, target, kind, created_at
from public.verdicts
order by actor, target, created_at desc;

-- ---------------------------------------------------------------- triggers
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, display_name, drink)
  values (
    new.id,
    coalesce(nullif(trim(new.raw_user_meta_data ->> 'display_name'), ''), split_part(coalesce(new.email, 'someone'), '@', 1)),
    coalesce(left(new.raw_user_meta_data ->> 'drink', 60), '')
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists profiles_touch on public.profiles;
create trigger profiles_touch
  before update on public.profiles
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------- row level security
alter table public.profiles enable row level security;
alter table public.verdicts enable row level security;
alter table public.messages enable row level security;

drop policy if exists "members see every member" on public.profiles;
create policy "members see every member" on public.profiles
  for select to authenticated using (true);

drop policy if exists "insert own profile" on public.profiles;
create policy "insert own profile" on public.profiles
  for insert to authenticated with check (auth.uid() = id);

drop policy if exists "edit own profile" on public.profiles;
create policy "edit own profile" on public.profiles
  for update to authenticated using (auth.uid() = id) with check (auth.uid() = id);

drop policy if exists "the tab is public to members" on public.verdicts;
create policy "the tab is public to members" on public.verdicts
  for select to authenticated using (true);

drop policy if exists "give your own verdict" on public.verdicts;
create policy "give your own verdict" on public.verdicts
  for insert to authenticated with check (auth.uid() = actor);

drop policy if exists "read your own chats" on public.messages;
create policy "read your own chats" on public.messages
  for select to authenticated using (auth.uid() = sender or auth.uid() = recipient);

drop policy if exists "send as yourself" on public.messages;
create policy "send as yourself" on public.messages
  for insert to authenticated with check (auth.uid() = sender);

grant usage on schema public to authenticated;
grant select, insert, update on public.profiles to authenticated;
grant select, insert on public.verdicts to authenticated;
grant select, insert on public.messages to authenticated;
grant select on public.current_verdicts to authenticated;

-- ---------------------------------------------------------------- realtime
do $$
begin
  if not exists (select 1 from pg_publication_tables where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'verdicts') then
    alter publication supabase_realtime add table public.verdicts;
  end if;
  if not exists (select 1 from pg_publication_tables where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'messages') then
    alter publication supabase_realtime add table public.messages;
  end if;
end $$;

-- ---------------------------------------------------------------- storage: avatars
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('avatars', 'avatars', true, 3145728, array['image/jpeg', 'image/png', 'image/webp'])
on conflict (id) do update set public = true, file_size_limit = excluded.file_size_limit, allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "avatars are public" on storage.objects;
create policy "avatars are public" on storage.objects
  for select using (bucket_id = 'avatars');

drop policy if exists "upload your own avatar" on storage.objects;
create policy "upload your own avatar" on storage.objects
  for insert to authenticated
  with check (bucket_id = 'avatars' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists "replace your own avatar" on storage.objects;
create policy "replace your own avatar" on storage.objects
  for update to authenticated
  using (bucket_id = 'avatars' and (storage.foldername(name))[1] = auth.uid()::text)
  with check (bucket_id = 'avatars' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists "remove your own avatar" on storage.objects;
create policy "remove your own avatar" on storage.objects
  for delete to authenticated
  using (bucket_id = 'avatars' and (storage.foldername(name))[1] = auth.uid()::text);
