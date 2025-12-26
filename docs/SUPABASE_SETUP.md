# Supabase Setup Guide

## Step 1: Create Supabase Project

1. Go to [supabase.com](https://supabase.com) and sign up/login
2. Click "New Project"
3. Choose your organization (or create one)
4. Set:
   - **Name**: `oread` (or your preference)
   - **Database Password**: Generate a strong password (save it!)
   - **Region**: Choose closest to you
5. Click "Create new project" and wait ~2 minutes

## Step 2: Get Your Credentials

Once the project is ready:

1. Go to **Settings** → **API**
2. Copy these values:

| Credential | Where to Find | Description |
|------------|---------------|-------------|
| Project URL | `URL` field | `https://xxxxx.supabase.co` |
| anon key | `anon` `public` | For client-side (public) |
| service_role key | `service_role` `secret` | For server-side (keep secret!) |

## Step 3: Add to Environment

Add to your `~/.zshrc`:

```bash
# Supabase (Oread)
export SUPABASE_URL="https://your-project-id.supabase.co"
export SUPABASE_ANON_KEY="your-anon-key"
export SUPABASE_SERVICE_KEY="your-service-role-key"
```

Then reload:
```bash
source ~/.zshrc
```

## Step 4: Run Database Schema

1. Go to **SQL Editor** in Supabase dashboard
2. Click "New query"
3. Copy the contents of `database/schema.sql` from this project
4. Click "Run" (or Cmd+Enter)
5. Verify tables created in **Table Editor**

## Step 5: Enable Row Level Security

The schema includes RLS policies, but verify they're enabled:

1. Go to **Authentication** → **Policies**
2. Ensure each table shows policies are active

## Step 6: Test Connection

```bash
cd /path/to/oread
source .venv/bin/activate
python -c "from src.db import get_client; print(get_client())"
```

Should print the Supabase client object without errors.

---

## Credentials Checklist

- [ ] SUPABASE_URL set in ~/.zshrc
- [ ] SUPABASE_ANON_KEY set in ~/.zshrc
- [ ] SUPABASE_SERVICE_KEY set in ~/.zshrc
- [ ] Schema SQL executed successfully
- [ ] Tables visible in Table Editor
- [ ] RLS policies active
- [ ] Test connection works

## Troubleshooting

### "Invalid API key"
- Check you copied the full key (they're long)
- Verify `~/.zshrc` was sourced

### "relation does not exist"
- Schema wasn't run - execute `database/schema.sql`

### "permission denied"
- RLS policies may be blocking - check Authentication → Policies

### Connection timeout
- Check SUPABASE_URL is correct
- Verify project is active (not paused)
