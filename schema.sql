-- Run this once in the Supabase SQL editor (Project -> SQL Editor -> New query).
-- Creates the table both report scripts upsert into every week.

create table if not exists domain_weekly_stats (
  id           bigint generated always as identity primary key,
  week_start   date not null,
  week_end     date not null,
  domain       text not null,
  sent         integer not null default 0,
  bounced      integer not null default 0,
  replied      integer not null default 0,
  bounce_rate  numeric generated always as (
                 round(case when sent > 0 then bounced::numeric / sent * 100 else 0 end, 2)
               ) stored,
  reply_rate   numeric generated always as (
                 round(case when sent > 0 then replied::numeric / sent * 100 else 0 end, 2)
               ) stored,
  created_at   timestamptz not null default now(),
  unique (domain, week_start)
);

-- Speeds up "latest week" / trend-over-time queries in Grafana.
create index if not exists idx_domain_weekly_stats_week
  on domain_weekly_stats (week_start desc);

-- Mailbox-level detail, powers the Domain/Mailbox toggle in explorer.html.
create table if not exists mailbox_weekly_stats (
  id           bigint generated always as identity primary key,
  mailbox_id   bigint not null,
  email        text not null,
  week_start   date not null,
  week_end     date not null,
  domain       text not null,
  sent         integer not null default 0,
  bounced      integer not null default 0,
  replied      integer not null default 0,
  daily_limit  integer,
  bounce_rate  numeric generated always as (
                 round(case when sent > 0 then bounced::numeric / sent * 100 else 0 end, 2)
               ) stored,
  reply_rate   numeric generated always as (
                 round(case when sent > 0 then replied::numeric / sent * 100 else 0 end, 2)
               ) stored,
  created_at   timestamptz not null default now(),
  unique (mailbox_id, week_start)
);

create index if not exists idx_mailbox_weekly_stats_week
  on mailbox_weekly_stats (week_start desc);
