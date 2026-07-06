# Mechanism Comparison

Goal: explain why Plus One chooses short-lived anonymous one-to-one matching instead of a general social feed or group chat.

This is not a broad competitor analysis. It compares the specific mechanics that influence temporary companion matching.

## Comparison Table

| Product or channel | Discovery mechanic | Interest confirmation | Conversation model | Identity exposure | Time pressure | Handoff to offline | Strength | Weakness for Plus One use case |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Tinder | Swipe queue | Mutual like | Chat after match | Profile-first | Low to medium | User-managed | Fast binary decisions | Dating context and permanent profile baggage |
| WhatsApp / WeChat direct chat | Contact list | Manual invitation | Lightweight direct chat | Real identity | Low | User-managed | Familiar and trusted for known contacts | Weak for finding who is available outside existing circles |
| Campus group chat | Broadcast message | Public replies or DMs | Many-to-many then private | Often semi-known | Medium | User-managed | Fast access to a large group | Social pressure, noisy, easy to ignore |
| Instagram /朋友圈 story | Passive broadcast | Reaction or DM | Private follow-up | Profile-first | Medium | User-managed | Low-effort broadcast | Audience mismatch and weak intent signal |
| Discord / Telegram community | Channel post | Replies or reactions | Channel plus DMs | Handle-based | Medium | User-managed | Good for interest communities | Less campus-location-specific by default |
| Plus One | Temporary campus card queue | Interested creates one-to-one match | Five-minute anonymous chat | Anonymous until handoff | High | Both agree before handoff | Optimized for low-commitment immediate plans | Needs trust, moderation, and enough local supply |

## Product Position

Plus One is designed for:

- Immediate or near-immediate campus plans.
- Lightweight exploration before commitment.
- A single companion, not a public social event.
- Anonymous first contact with explicit handoff only after mutual agreement.

Plus One is not designed for:

- Dating profiles.
- Long-term social networking.
- Permanent identity feeds.
- Group event management.
- Public community discussion.

## Design Implications

- Keep `Interested` and `Pass` as primary discovery actions because they reduce social risk.
- Keep one-to-one matching because the product promise is "find a Plus One", not "host a group".
- Keep five-minute chat pressure because temporary plans lose value quickly.
- Keep handoff behind mutual agreement because offline meeting is the moment of highest trust need.
- Keep anonymous sessions for MVP speed, but plan a verified student mode before a real campus launch.
