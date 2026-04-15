from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Table, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


prompt_node_tags = Table(
    "prompt_node_tags",
    Base.metadata,
    Column(
        "prompt_node_id",
        ForeignKey("prompt_node.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tag_id",
        ForeignKey("tag.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

image_tags = Table(
    "image_tags",
    Base.metadata,
    Column(
        "image_id",
        ForeignKey("image.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tag_id",
        ForeignKey("tag.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Project(Base):
    __tablename__ = "project"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    nodes: Mapped[list["PromptNode"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class PromptNode(Base):
    __tablename__ = "prompt_node"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE")
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_node.id", ondelete="CASCADE"), default=None
    )
    name: Mapped[str | None] = mapped_column(String(255), default=None)
    prompt_text: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    is_starred: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    project: Mapped["Project"] = relationship(back_populates="nodes")
    parent: Mapped["PromptNode | None"] = relationship(
        back_populates="children", remote_side=[id]
    )
    children: Mapped[list["PromptNode"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    image: Mapped["Image | None"] = relationship(
        back_populates="prompt_node", uselist=False, cascade="all, delete-orphan"
    )
    coaching_messages: Mapped[list["CoachingMessage"]] = relationship(
        back_populates="prompt_node",
        cascade="all, delete-orphan",
        order_by="CoachingMessage.created_at",
    )
    tags: Mapped[list["Tag"]] = relationship(
        secondary=prompt_node_tags, back_populates="prompt_nodes"
    )


class Image(Base):
    __tablename__ = "image"

    id: Mapped[int] = mapped_column(primary_key=True)
    prompt_node_id: Mapped[int] = mapped_column(
        ForeignKey("prompt_node.id", ondelete="CASCADE"), unique=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    feedback: Mapped[str | None] = mapped_column(Text, default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    # Provenance for MJ results. Null for uploaded images. Snowflakes
    # stored as strings — int64 fits, but strings are friendlier with
    # SQLite and we never do arithmetic on them. Populated for kind in
    # {"grid", "upscale", "variation"} so we can press U/V buttons on
    # the original Discord message to spawn child images.
    discord_message_id: Mapped[str | None] = mapped_column(String(32), default=None)
    discord_channel_id: Mapped[str | None] = mapped_column(String(32), default=None)
    # "uploaded" | "grid" | "upscale" | "variation". Gates the UI —
    # only grids get quadrant action buttons. Plain string rather than
    # Enum to keep SQLite migrations painless.
    kind: Mapped[str] = mapped_column(String(20), default="uploaded")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    prompt_node: Mapped["PromptNode"] = relationship(back_populates="image")
    tags: Mapped[list["Tag"]] = relationship(
        secondary=image_tags, back_populates="images"
    )


class Tag(Base):
    __tablename__ = "tag"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)

    prompt_nodes: Mapped[list["PromptNode"]] = relationship(
        secondary=prompt_node_tags, back_populates="tags"
    )
    images: Mapped[list["Image"]] = relationship(
        secondary=image_tags, back_populates="tags"
    )


class CoachingMessage(Base):
    __tablename__ = "coaching_message"

    id: Mapped[int] = mapped_column(primary_key=True)
    prompt_node_id: Mapped[int] = mapped_column(
        ForeignKey("prompt_node.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    prompt_node: Mapped["PromptNode"] = relationship(back_populates="coaching_messages")
